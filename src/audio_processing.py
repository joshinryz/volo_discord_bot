"""Audio processing for the Discord bot."""
import io
import time
import subprocess
from queue import Queue
import os
import asyncio
from threading import Thread, Lock
import yaml
import torch

import discord
import whisper
from discord.sinks import *

USE_CUDA = True
CORRECT_SPELLINGS = "ChatGPT, Foundry, D&D, Lysanthir, Sid, Charles, Prestidigitation, Luna, Spelljammer, Eldritch Blast, Spotify, Luminous Bolt, Swing and a missile, Thaumaturgy"
TODAY_STRING = time.strftime("%Y%m%d-%H")
WHISPER_MODEL = "medium.en"
WHISPER_LANGUAGE = "en"
TIME_BETWEEN_SPEECH = 2.0  # Time between speech segments in seconds
POST_TO_DISCORD = False  # Whether to post transcriptions to Discord

if not os.path.exists(f"./Sessions/{TODAY_STRING}"):
    os.makedirs(f"./Sessions/{TODAY_STRING}")


def load_player_map(filename="player_map.yml"):
    """Load the player map YAML file."""
    with open(filename, "r", encoding="utf-8") as file:
        data = yaml.safe_load(file)

    # Properly parse the YAML data
    player_map = {}
    for user_entry in data["users"]:
        for user_id, details in user_entry.items():
            player_map[str(user_id)] = details
    return player_map


class SampleData:
    """Class to store audiosample data."""

    def __init__(self, file):
        self.file = file
        self.start_time = time.time()

    def write(self, data):
        """Write data to the file."""
        try:
            self.file.write(data)
        except ValueError:
            pass

    def cleanup(self):
        """Cleanup the file."""
        self.file.seek(0)

    def on_format(self, encoding):
        """Called when the audio format is set."""
        pass


class CustomSink(discord.sinks.MP3Sink):
    """Custom sink to handle audio data."""

    def __init__(self, bot, ctx, filters=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.channel = ctx.channel
        self.player_map = load_player_map()
        self.transcription_lock = Lock()
        self.transcription_file = f"./Sessions/{TODAY_STRING}/chat_transcription.txt"

        if filters is None:
            filters = default_filters

        if filters is None:
            filters = {}  # Default value if not provided
        self.filters = filters

        self.encoding = "mp3"
        self.audio_data = {}
        self.audio_buffer = {}
        self.audio_user_timers = {}
        self.audio_processing_queue = Queue()
        self.thread = Thread(target=self.worker, args=(self.audio_processing_queue,))

    def get_user_details(self, user_id):
        """Get a formatted string with player and character names based on user ID."""
        details = self.player_map.get(f"{user_id}", {})
        player = details.get("player", None)
        character = details.get("character", None)

        if player and character:
            return f"{character}({player})"
        else:
            return str(user_id)  # Fallback to user ID

    def trim_silence(self, file_path):
        """Check if the audio file's volume exceeds a certain threshold."""
        command = [
            "ffmpeg",
            "-y",
            "-i",
            file_path,
            "-af",
            "areverse,silenceremove=start_periods=1:start_duration=0.05:start_silence=0.1:start_threshold=0.02,areverse,silenceremove=start_periods=1:start_duration=0.05:start_silence=0.1:start_threshold=0.02",
            file_path.replace("-original.mp3", ".mp3"),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        print(result.stderr)
        return

    def transcribe_audio_in_queue(self, file_path, user, start_time=None):
        """Use a background thread to process audio asyncronously."""
        transcription = self.transcribe_audio(file_path)
        print(f"Transcription for {user}: {transcription}")
        # delete file when done
        os.remove(file_path)
        # self.send_transcription_as_user(user, transcription)
        name_or_id = self.get_user_details(user)
        with self.transcription_lock:
            with open(self.transcription_file, "a", encoding="utf-8") as f:
                f.write(f"{start_time} : {name_or_id} : {transcription}\n")

    # Worker function that processes items from the queue
    def worker(self, queue):
        while True:
            item = queue.get()  # Retrieve an item from the queue
            if item is None:
                break  # Exit condition
            self.transcribe_audio_in_queue(
                item["file"], item["user"], item["start_time"]
            )
            queue.task_done()

    def transcribe_audio(self, file_path):
        """Call whisper to transcribe audio."""
        device = (
            "cuda" if torch.cuda.is_available() and USE_CUDA else "cpu"
        )  # Check if CUDA is available
        model = whisper.load_model(WHISPER_MODEL, device=device)
        if os.path.exists(file_path) and os.path.getsize(file_path) > 1024:
            result = model.transcribe(
                file_path,
                initial_prompt=CORRECT_SPELLINGS,
                language=WHISPER_LANGUAGE,
                word_timestamps=True,
            )
            return result["text"]
        else:
            return ""

    @Filters.container
    def write(self, data, user):
        """Override the write method to save audio data to a file."""
        if user not in self.audio_data:
            file = io.BytesIO()
            self.audio_data.update({user: AudioData(file)})

        if user not in self.audio_buffer:
            sample = io.BytesIO()
            self.audio_buffer.update({user: SampleData(sample)})

        if user not in self.audio_user_timers:
            self.audio_user_timers.update({user: time.time()})

        if time.time() - self.audio_user_timers[user] > TIME_BETWEEN_SPEECH:
            self.save_speech_segment(self.audio_buffer, user=user)

        sample = self.audio_buffer[user]
        sample.write(data)

        file = self.audio_data[user]
        file.write(data)
        self.audio_user_timers[user] = time.time()

    def save_speech_segment(self, audio_buffer, user):
        """Save a speech segment to a file and transcribe it."""
        file_name = f"{user}_{int(time.time())}-original.{self.encoding}"
        trimmed_name = f"{user}_{int(time.time())}.{self.encoding}"
        audio_buffer[user].file.seek(0)
        self.convert_to_mp3_and_save(audio_buffer[user].file, file_name)
        del audio_buffer[user]
        audio_buffer.update({user: SampleData(io.BytesIO())})

        # Transcribe the saved mp3 file
        # Start a background thread for transcription
        # Check if the audio is significant before transcribing
        self.trim_silence(f"./Recordings/{file_name}")
        item = {}
        item["user"] = user
        item["file"] = f"./Recordings/{trimmed_name}"
        item["start_time"] = audio_buffer[user].start_time
        self.audio_processing_queue.put(item)
        if not self.thread.is_alive():
            self.thread.start()

    def convert_to_mp3_and_save(self, audio_data, file_name):
        """Convert audio data to mp3 and save it to a file."""
        args = [
            "ffmpeg",
            "-f",
            "s16le",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-i",
            "-",
            "-f",
            "mp3",
            "pipe:1",
        ]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stdin=subprocess.PIPE)
        out = process.communicate(input=audio_data.read())[0]
        with open(f"./Recordings/{file_name}", "wb") as f:
            f.write(out)


async def once_done(sink, channel: discord.TextChannel, *args):
    """Called once the recording is done."""
    recorded_users = [  # A list of recorded users
        f"<@{user_id}>" for user_id, audio in sink.audio_data.items()
    ]
    print(f"finished recording audio for: {', '.join(recorded_users)}.")
    await sink.vc.disconnect()  # Disconnect from the voice channel.
    # Create a folder for the current day if it doesn't exist

    for user_id, audio in sink.audio_data.items():
        with open(f"./Sessions/{TODAY_STRING}/{user_id}.{sink.encoding}", "wb+") as f:
            f.write(audio.file.read())
