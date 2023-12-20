# audio_processing.py
import discord
import io
import time
import subprocess
import os
import whisper
import asyncio
from threading import Thread, Lock
from discord.sinks import *
import yaml

TODAY_STRING = time.strftime("%Y%m%d-%H")
WHISPER_MODEL="medium.en"
TIME_BETWEEN_SPEECH = 0.75  # Time between speech segments in seconds

if not os.path.exists(f"./Sessions/{TODAY_STRING}"):
    os.makedirs(f"./Sessions/{TODAY_STRING}")

def load_player_map(filename='player_map.yml'):
    with open(filename, 'r', encoding='utf-8') as file:
        data = yaml.safe_load(file)
    
    # Properly parse the YAML data
    player_map = {}
    for user_entry in data['users']:
        for user_id, details in user_entry.items():
            player_map[str(user_id)] = details
    return player_map
    
class SampleData:
    def __init__(self, file):
        self.file = file
        self.start_time = time.time()

    def write(self, data):
        try:
            self.file.write(data)
        except ValueError:
            pass

    def cleanup(self):
        self.file.seek(0)

    def on_format(self, encoding):
        pass

class CustomSink(discord.sinks.MP3Sink):
    def __init__(self, bot, ctx, filters=None, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.bot = bot
        self.channel = ctx.channel
        self.player_map = load_player_map() 
        self.transcription_lock = Lock()
        self.transcription_file = f'./Sessions/{TODAY_STRING}/chat_transcription.txt' 

        if filters is None:
            filters = default_filters
        
        if filters is None:
            filters = {}  # Default value if not provided
        self.filters = filters

        self.encoding = "mp3"
        self.audio_data = {}
        self.audio_buffer = {}
        self.audio_user_timers = {}
    
        
    def get_user_details(self, user_id):
        """Get a formatted string with player and character names based on user ID."""
        details = self.player_map.get(f'{user_id}', {})
        player = details.get('player', None)
        character = details.get('character', None)

        if player and character:
            return f"{character}({player})"
        else:
            return str(user_id)  # Fallback to user ID

    def is_audio_significant(self, file_path, threshold=-30):
        """Check if the audio file's volume exceeds a certain threshold."""
        command = [
            'ffmpeg', '-i', file_path, '-af', 'volumedetect', '-vn', '-sn', '-dn', 
            '-f', 'null', '/dev/null'
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        output = result.stderr

        # Extract the mean volume level
        mean_volume = None
        for line in output.split('\n'):
            if 'mean_volume' in line:
                mean_volume = float(line.split(':')[1].strip().replace(' dB', ''))
                break

        if mean_volume is None:
            return False  # Unable to determine, defaulting to False

        return mean_volume > threshold
    
    def send_transcription_as_user(self, user_id, transcription):
        user = next((member for member in self.channel.members if member.id == user_id), None)

        if user:
            user_name = user.name
            message_content = f"{user_name} said: {transcription}"
            # if avatar_url:
            #     message_content += f"\n{avatar_url}"

            asyncio.run_coroutine_threadsafe(
                self.channel.send(message_content),
                self.bot.loop
            )
        else:
            # Fallback if the user can't be found
            asyncio.run_coroutine_threadsafe(
                self.channel.send(f"User ID {user_id} said: {transcription}"),
                self.bot.loop
            )
    
    def transcribe_audio_in_background(self, file_path, user, start_time=None):
        transcription = self.transcribe_audio(file_path)
        print(f"Transcription for {user}: {transcription}")
         # delete file when done
        os.remove(file_path)
        self.send_transcription_as_user(user, transcription)
        name_or_id = self.get_user_details(user)
        with self.transcription_lock:
            with open(self.transcription_file, 'a', encoding='utf-8') as f:
                f.write(f"{start_time} : {name_or_id} : {transcription}\n")

    
    def transcribe_audio(self, file_path):
        model = whisper.load_model("base")
        result = model.transcribe(file_path)
        return result['text']
    
    @Filters.container  
    def write(self, data, user):
        if user not in self.audio_data:
            file = io.BytesIO()
            self.audio_data.update({user: AudioData(file)})
        
        if user not in self.audio_buffer:
            sample = io.BytesIO()
            self.audio_buffer.update({user: SampleData(sample)})
        
        if user not in self.audio_user_timers:
            self.audio_user_timers.update({user: time.time()})

        if  time.time() - self.audio_user_timers[user] > TIME_BETWEEN_SPEECH:
            self.save_speech_segment(self.audio_buffer, user=user)
        
        sample = self.audio_buffer[user]
        sample.write(data)

        file = self.audio_data[user]
        file.write(data)
        self.audio_user_timers[user] = time.time()

    def save_speech_segment(self, audio_buffer, user):
        file_name = f"{user}_{int(time.time())}.{self.encoding}"
        audio_buffer[user].file.seek(0)
        self.convert_to_mp3_and_save(audio_buffer[user].file, file_name)
        del audio_buffer[user]
        audio_buffer.update({user: SampleData(io.BytesIO())})
        
        # Transcribe the saved mp3 file
        # Start a background thread for transcription
         # Check if the audio is significant before transcribing
        if self.is_audio_significant(f'./Recordings/{file_name}'):
            thread = Thread(target=self.transcribe_audio_in_background, args=(f'./Recordings/{file_name}', user, audio_buffer[user].start_time))
            thread.start()
            
        else:
            # Handle silent audio (e.g., log it or notify)
            print(f"Audio from {user} is silent or too low volume. Skipping transcription.")
            os.remove(f'./Recordings/{file_name}')

    def convert_to_mp3_and_save(self, audio_data, file_name):
        args = [
            "ffmpeg",
            "-f", "s16le",
            "-ar", "48000",
            "-ac", "2",
            "-i", "-",
            "-f", "mp3",
            "pipe:1"
        ]
        process = subprocess.Popen(args, stdout=subprocess.PIPE, stdin=subprocess.PIPE)
        out = process.communicate(input=audio_data.read())[0]
        with open(f'./Recordings/{file_name}', 'wb') as f:
            f.write(out)

    def transcribe_audio(self, file_path):
        model = whisper.load_model(WHISPER_MODEL)
        result = model.transcribe(file_path)
        return result['text']

async def once_done(sink, channel: discord.TextChannel, *args):
    recorded_users = [  # A list of recorded users
        f"<@{user_id}>"
        for user_id, audio in sink.audio_data.items()]
    print(f"finished recording audio for: {', '.join(recorded_users)}.")
    await sink.vc.disconnect()  # Disconnect from the voice channel.
    # Create a folder for the current day if it doesn't exist

    for user_id, audio in sink.audio_data.items():    
        with open(f'./Sessions/{TODAY_STRING}/{user_id}.{sink.encoding}', 'wb+') as f:
            f.write(audio.file.read())
