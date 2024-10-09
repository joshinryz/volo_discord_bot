import asyncio
from concurrent.futures import ThreadPoolExecutor
import io
import logging
import json
import re
import threading
import time
import wave
import numpy as np
from datetime import datetime
from queue import Queue
import torch
import tempfile
from tempfile import NamedTemporaryFile
from typing import List

import speech_recognition as sr
from discord.sinks.core import Filters, Sink, default_filters
from openai import OpenAI

client = OpenAI()

from faster_whisper import WhisperModel

WHISPER_MODEL = "large-v3"
WHISPER_LANGUAGE = "en"
device = "cuda" if torch.cuda.is_available() else "cpu"

audio_model = WhisperModel(WHISPER_MODEL, device=device, compute_type="float32")


# Set the model to evaluation mode (important for inference)
logger = logging.getLogger(__name__)


class Speaker:
    """
    A class to store the audio data and transcription for each user.
    """

    def __init__(self, user, data, time=time.time()):
        self.user = user
        self.data = [data]
        self.first_word =time
        self.last_word = time
        self.last_phrase = time

        self.word_timeout = 0

        self.phrase = ""

        self.empty_bytes_counter = 0
        self.new_bytes = 1


class WhisperSink(Sink):
    """A sink for discord that takes audio in a voice channel and transcribes it for each user.

    Uses faster whisper for transcription. can be swapped out for other audio transcription libraries pretty easily.

    :param transcript_queue: The queue to send the transcription output to
    :param filters: Some discord thing I'm not sure about
    :param data_length: The amount of data to save when user is silent but their mic is still active
    :param quiet_phrase_timeout: A larger timeout for when the transcription has detected the user is in mid sentence
    :param mid_sentence_multiplier: A smaller timeout when the transcription has detected the user has finished a sentence
    :param no_data_multiplier: If the user has stopped talking on discord completely (Their icon is no longer green), reduce both timeouts by a percantage to improve inference time
    :param max_phrase_timeout: Send out the current transcription after x seconds if the user continues to talk for a long period
    :param min_phrase_length: Minimum length of transcription to reduce noise
    :param max_speakers: The amount of users to transcribe when all speakers are talking at once.
    """

    def __init__(
        self,
        transcript_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
        *,
        filters=None,
        shared_ctx=None,
        data_length=50000,
        quiet_phrase_timeout=1,
        mid_sentence_multiplier=1.8,
        no_data_multiplier=0.75,
        max_phrase_timeout=30,
        min_phrase_length=3,
        max_speakers=-1,
    ):
        self.queue = transcript_queue
        self.loop = loop

        if filters is None:
            filters = default_filters
        self.filters = filters
        Filters.__init__(self, **self.filters)
        self.shared_ctx = shared_ctx
        self.data_length = data_length
        self.quiet_phrase_timeout = quiet_phrase_timeout
        self.mid_sentence_multiplier = mid_sentence_multiplier
        self.no_data_multiplier = no_data_multiplier
        self.max_phrase_timeout = max_phrase_timeout
        self.min_phrase_length = min_phrase_length
        self.max_speakers = max_speakers

        self.vc = None
        self.audio_data = {}
        self.running = True
        self.speakers: List[Speaker] = []
        self.voice_queue = Queue()
        self.executor = ThreadPoolExecutor(max_workers=8)  # TODO: Adjust this

    def start_voice_thread(self, on_exception=None):
        def thread_exception_hook(args):
            logger.debug(
                f"""Exception in voice thread: {args} Likely disconnected while listening."""
            )

        logger.debug(
            f"Starting whisper sink thread for guild {self.vc.channel.guild.id}."
        )
        self.voice_thread = threading.Thread(
            target=self.insert_voice, args=(), daemon=True
        )

        if on_exception:
            threading.excepthook = on_exception
        else:
            threading.excepthook = thread_exception_hook

        self.voice_thread.start()

    def stop_voice_thread(self):
        self.running = False
        try:
            self.voice_thread.join()
        except Exception as e:
            logger.error(f"Unexpected error during thread join: {e}")
        finally:
            logger.debug(
                f"A sink thread was stopped for guild {self.vc.channel.guild.id}."
            )
    def check_audio_length(self, temp_file):
        # Ensure the BytesIO is at the start
        temp_file.seek(0)

        # Open the BytesIO object as a WAV file
        with wave.open(temp_file, 'rb') as wave_file:
            # Get the number of frames
            frames = wave_file.getnframes()
            # Get the frame rate (samples per second)
            frame_rate = wave_file.getframerate()
            # Calculate the duration in seconds
            duration = frames / float(frame_rate)

        return duration
    def transcribe_audio(self, temp_file):
        try:
            if self.check_audio_length(temp_file) <= 0.1:
                return ""
            openai_transcription = client.audio.transcriptions.create(
                file=("foobar.wav", temp_file),
                model="whisper-1",
            )
            
            temp_file.seek(0)
               
            # The whisper model
            segments, info = audio_model.transcribe(
                temp_file,
                language=WHISPER_LANGUAGE,
                beam_size=10,
                best_of=3,
                vad_filter=True,
                vad_parameters=dict(
                    min_silence_duration_ms=150,
                    threshold=0.8
                ),
                no_speech_threshold=0.6,
                initial_prompt="You are writing the transcriptions for a D&D game.",
            )

            segments = list(segments)
            result = ""
            for segment in segments:
                result += segment.text

            logger.info(f"Transcription: {result}")
            logger.info(f"OpenAI Transcription: {openai_transcription.text}")
            return result
        except Exception as e:
            logger.error(f"Error transcribing audio: {e}")
            return ""

    def transcribe(self, speaker: Speaker):
        audio_data = sr.AudioData(
            bytes().join(speaker.data),
            self.vc.decoder.SAMPLING_RATE,
            self.vc.decoder.SAMPLE_SIZE // self.vc.decoder.CHANNELS,
        )

        wav_data = io.BytesIO(audio_data.get_wav_data())

        wav_io = io.BytesIO()
        with wave.open(wav_io, "wb") as wave_writer:
            wave_writer.setnchannels(self.vc.decoder.CHANNELS)
            wave_writer.setsampwidth(
                self.vc.decoder.SAMPLE_SIZE // self.vc.decoder.CHANNELS)
            wave_writer.setframerate(self.vc.decoder.SAMPLING_RATE)
            wave_writer.writeframes(wav_data.getvalue())

        wav_io.seek(0)
        # Check if the audio is long enough to transcribe, else return empty string
        
        transcription = self.transcribe_audio(wav_io)

        return transcription

    def insert_voice(self):
        while self.running:
            try:
                # Process the voice_queue
                while not self.voice_queue.empty():
                    item = self.voice_queue.get()
                    # Find or create a speaker
                    speaker = next(
                        (s for s in self.speakers if s.user == item[0]), None
                    )
                    if speaker:
                        speaker.data.append(item[1])
                        speaker.new_bytes += 1
                        speaker.last_word = item[2]
                    elif (
                        self.max_speakers < 0 or len(self.speakers) <= self.max_speakers
                    ):
                        self.speakers.append(Speaker(item[0], item[1], item[2]))
                    
                    


                # Transcribe audio for each speaker
                # so this is interesting, as we arent checking the size of the audio stream, we are just transcribing it
                future_to_speaker = {}
                for speaker in self.speakers:
                    if (time.time() - speaker.last_word) < 0.5:
                        # Lets make sure the user stopped talking.
                        continue
                    if speaker.new_bytes > 1:
                        speaker.new_bytes = 0
                        future = self.executor.submit(self.transcribe, speaker)
                        future_to_speaker[future] = speaker
                    else:
                        # No data coming in from discord, reduces word_timeout for faster inference
                        speaker.word_timeout = round(
                            speaker.word_timeout * self.no_data_multiplier, 2
                        )

                for future in future_to_speaker:
                    speaker = future_to_speaker[future]
                    try:
                        transcription = future.result()
                        current_time = time.time()
                        speaker_new_bytes = speaker.new_bytes

                        self.update_speaker_status(
                            speaker, transcription, current_time, speaker_new_bytes
                        )
                        self.check_speaker_timeouts(
                            speaker, transcription
                        )
                    except Exception as e:
                        logger.warn(f"Error in insert_voice future: {e}")

                

                # Loops with no wait time is bad
                time.sleep(0.1)
            except Exception as e:
                logger.error(f"Error in insert_voice: {e}")

    def check_speaker_timeouts(self, current_speaker, transcription):
        current_time = time.time()
        # Copy the list to avoid modification during iteration
        for speaker in self.speakers[:]:
            if current_speaker.user != speaker.user:
                continue
            word_timeout = speaker.word_timeout
            if len(speaker.phrase) >= self.min_phrase_length:
                # If the user stops saying anything new or has been speaking too long.
                if current_time - speaker.last_word > word_timeout:
                    logger.debug(
                        f"[time, word timeout]: [{current_time}, {word_timeout}]"
                    )
                    self.loop.call_soon_threadsafe(
                        self.queue.put_nowait,
                        {"user": speaker.user, "result": speaker.phrase},
                    )
                    self.write_transcription_log(speaker, transcription)
                    self.speakers.remove(speaker)
                elif current_time - speaker.last_phrase > self.max_phrase_timeout:
                    
                    logger.debug(
                        f"[time, max phrase timeout]: [{current_time}, {speaker.last_phrase}]"
                    )
                    self.loop.call_soon_threadsafe(
                        self.queue.put_nowait,
                        {"user": speaker.user, "result": speaker.phrase},
                    )
                    self.write_transcription_log(speaker, transcription)
                    self.speakers.remove(speaker)
            elif current_time - speaker.last_phrase > self.quiet_phrase_timeout * 2:
                # Remove the speaker if no valid phrase detected after set period of time
                logger.debug(
                    f"[time, phrase timeout]: [{current_time}, {speaker.last_phrase}]"
                )
                self.speakers.remove(speaker)

    def write_transcription_log(self, speaker, transcription):
        # Convert first_word and last_word Unix timestamps to datetime
        first_word_time = datetime.fromtimestamp(speaker.first_word).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]
        last_word_time = datetime.fromtimestamp(speaker.last_word).strftime('%Y-%m-%d %H:%M:%S.%f')[:-3]

        # Prepare the log data as a dictionary
        log_data = {
            "date": first_word_time[:10],                  # Date (from first_word)
            "begin": first_word_time[11:],       # First word time (HH:MM:SS.ss)
            "end": last_word_time[11:],         # Last word time (HH:MM:SS.ss)
            "user_id": speaker.user,                       # User ID
            "event_source": "Discord",                     # Event source
            "data": transcription                          # Transcription text
        }

        # Convert the log data to JSON
        log_message = json.dumps(log_data)

        # Get the transcription logger
        transcription_logger = logging.getLogger('transcription')

        # Log the message
        transcription_logger.info(log_message)
    
    def update_speaker_status(
        self, speaker, transcription, current_time, speaker_new_bytes
    ):
        # If the transcription is different from the last one, reset the word timeout
        if speaker.phrase != transcription:
            speaker.empty_bytes_counter = 0
            speaker.last_phrase = current_time
            speaker.word_timeout = self.quiet_phrase_timeout
            if re.search(r"\s*\.{2,}$", transcription) or not re.search(
                r"[.!?]$", transcription
            ):
                speaker.word_timeout = round(
                    speaker.word_timeout * self.mid_sentence_multiplier, 3
                )
            speaker.phrase = transcription
        elif speaker.empty_bytes_counter > 5:
            speaker.data = speaker.data[:-speaker_new_bytes]
        else:
            speaker.empty_bytes_counter += 1

    @Filters.container
    def write(self, data, user):
        """Gets audio data from discord for each user talking"""
        # Discord will send empty bytes from when the user stopped talking to when the user starts to talk again.
        # Its only the first data that grows massive and its only silent audio, so its trimmed.

        data_len = len(data)
        if data_len > self.data_length:
            data = data[-self.data_length :]
        write_time = time.time()
        # Send bytes to be transcribed
        self.voice_queue.put_nowait([user, data, write_time])

    def close(self):
        logger.debug("Closing whisper sink.")
        self.running = False
        self.queue.put_nowait(None)
        super().cleanup()
