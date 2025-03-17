import asyncio
import io
import json
import logging
import threading
import time
import wave
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from queue import Queue
from typing import List

import speech_recognition as sr
import torch
from discord.sinks.core import Filters, Sink, default_filters
from faster_whisper import WhisperModel
from openai import OpenAI

WHISPER_MODEL = "large-v3"
WHISPER_LANGUAGE = "en"
WHISPER__PRECISION = "float32"

DEVICE = "cuda" if torch.cuda.is_available() else "cpu"

# Set the model to evaluation mode (important for inference)
logger = logging.getLogger(__name__)

if DEVICE == "cuda":
    gpu_ram = torch.cuda.get_device_properties(0).total_memory/1024**3
    if gpu_ram < 5.0:
        logger.warning("GPU has less than 5GB of RAM. Switching to CPU.")
        DEVICE = "cpu"

audio_model = WhisperModel(WHISPER_MODEL, device=DEVICE, compute_type=WHISPER__PRECISION)



class Speaker:
    """
    A class to store the audio data and transcription for each user.
    """

    def __init__(self, user: int, player: str, character: str, data, time=time.time()):
        self.user = user
        self.player = player
        self.character = character
        self.data = [data]
        self.first_word =time
        self.last_word = time
        self.new_bytes = 1


class WhisperSink(Sink):
    """A sink for discord that takes audio in a voice channel and transcribes it for each user.

    Uses faster whisper for transcription. can be swapped out for other audio transcription libraries pretty easily.

    :param transcript_queue: The queue to send the transcription output to
    :param filters: Some discord thing I'm not sure about
    :param data_length: The amount of data to save when user is silent but their mic is still active

    :param max_speakers: The amount of users to transcribe when all speakers are talking at once.
    """

    def __init__(
        self,
        transcript_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
        transcriber_type="local",
        *,
        filters=None,
        player_map={},
        data_length=50000,
        max_speakers=-1,
    ):
        self.queue = transcript_queue
        self.transcription_output_queue = asyncio.Queue()
        self.loop = loop

        if filters is None:
            filters = default_filters
        self.filters = filters
        Filters.__init__(self, **self.filters)
        self.data_length = data_length
        self.max_speakers = max_speakers
        self.transcriber_type = transcriber_type
        if transcriber_type == "openai":
            self.client = OpenAI()
        self.vc = None
        self.audio_data = {}
        self.running = True
        self.speakers: List[Speaker] = []
        self.voice_queue = Queue()
        self.executor = ThreadPoolExecutor(max_workers=8)  # TODO: Adjust this
        self.player_map = player_map

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
            frames = wave_file.getnframes()
            frame_rate = wave_file.getframerate()
            duration = frames / float(frame_rate)
        return duration
    def transcribe_audio(self, temp_file):
        try:
            # Ensure that the audio is long enough to transcribe. If not, return an empty string
            if self.check_audio_length(temp_file) <= 0.1:
                return ""
            
            if self.transcriber_type == "openai":
                temp_file.seek(0)
                openai_transcription = self.client.audio.transcriptions.create(
                    file=("foobar.wav", temp_file),
                    model="whisper-1",
                    language=WHISPER_LANGUAGE,
                )
                logger.info(f"OpenAI Transcription: {openai_transcription.text}")
                return openai_transcription.text
            else:               
                # The whisper model
                temp_file.seek(0)
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
    
    def get_transcriptions(self):
        """Retrieve all transcriptions from the queue, format them to only include data, begin, and user_id."""
        transcriptions = []
        while not self.transcription_queue.empty():
            log_message = self.transcription_queue.get_nowait()

            # Assuming log_message is a dictionary (or string in JSON format)
            if isinstance(log_message, str):
                log_message = json.loads(log_message)  # Convert from string to dictionary if needed

            # Extract only the desired fields from the log message
            begin = log_message.get("begin", "Unknown begin")
            user_id = log_message.get("user_id", "Unknown user")
            data = log_message.get("data", "")

            # Format the transcription entry with only the relevant fields
            formatted_entry = (
                f"Begin: {begin}\n"
                f"User ID: {user_id}\n"
                f"Data: {data}\n"
                "-------------------------\n"
            )

            # Add the formatted entry to the transcription list
            transcriptions.append(formatted_entry)

        return transcriptions
    
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
                        user_id = item[0]
                        user_map = self.player_map.get(user_id, {})
                        player = user_map.get("player")
                        character = user_map.get("character")
                        self.speakers.append(Speaker(user_id, player, character, item[1], item[2]))
                    
                    


                # Transcribe audio for each speaker
                # so this is interesting, as we arent checking the size of the audio stream, we are just transcribing it
                future_to_speaker = {}
                for speaker in self.speakers:
                    if (time.time() - speaker.last_word) < 1.5:
                        # Lets make sure the user stopped talking.
                        continue
                    if speaker.new_bytes > 1:
                        speaker.new_bytes = 0
                        future = self.executor.submit(self.transcribe, speaker)
                        future_to_speaker[future] = speaker
                    else:
                        continue
                
                for future in future_to_speaker:
                    speaker = future_to_speaker[future]
                    try:
                        transcription = future.result()
                        current_time = time.time()
                        speaker_new_bytes = speaker.new_bytes
                        # Remove speaker once returned. 
                        for s in self.speakers[:]:
                            if speaker.user == s.user:
                                self.write_transcription_log(s, transcription)
                                self.speakers.remove(s)

                    except Exception as e:
                        logger.warn(f"Error in insert_voice future: {e}")

            except Exception as e:
                logger.error(f"Error in insert_voice: {e}")

    def check_speaker_timeouts(self, current_speaker, transcription):

        # Copy the list to avoid modification during iteration
        for speaker in self.speakers[:]:
            if current_speaker.user == speaker.user:
                self.write_transcription_log(speaker, transcription)
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
            "player": speaker.player,
            "character": speaker.character,
            "event_source": "Discord",                     # Event source
            "data": transcription                          # Transcription text
        }

        # Convert the log data to JSON
        log_message = json.dumps(log_data)

        # Get the transcription logger
        transcription_logger = logging.getLogger('transcription')
        # Log the message
        transcription_logger.info(log_message)
        # Place into queue for processing
        self.transcription_output_queue.put_nowait(log_message)
    

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
