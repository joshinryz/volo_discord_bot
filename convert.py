import yaml
import json
import os
import argparse
from datetime import datetime

# use argparse to get the -file argument from the command line
parser = argparse.ArgumentParser()
parser.add_argument("-file", help="The path to the log file to convert")
args = parser.parse_args()
if not args.file:
    raise ValueError("Please provide a path to the log file using the -file argument")

try:
    with open(args.file, "r") as f:
        log_content = f.read()
except FileNotFoundError:
    raise FileNotFoundError(f"File not found: {args.file}") 

# Assuming player_map.yml is in the same directory as the log file
player_file_yaml = os.path.join(os.path.dirname(args.file), "player_map.yml")
with open(player_file_yaml, "r") as f:
    player_map_yaml = f.read()

# Parse the YAML data
player_map = yaml.safe_load(player_map_yaml)

# Create a dictionary mapping Discord IDs to character names
user_id_to_character = {str(list(user.keys())[0]): list(user.values())[0]['character'] for user in player_map['users']}

# Function to convert a log line and replace user_id with the character name
def convert_log_line(log_line, user_map):
    log_data = json.loads(log_line)
    user_id = str(log_data['user_id'])
    log_data['character'] = user_map.get(user_id, "Unknown")
    return log_data

# Split log content into lines
log_lines = log_content.strip().split("\n")

# Convert each line in the log file to structured data and filter out empty 'data' lines
converted_log_lines = [
    convert_log_line(line, user_id_to_character) for line in log_lines
    if json.loads(line)['data'].strip()  # Filter out lines where 'data' is empty
]

# Sort the log lines by date and begin time
converted_log_lines.sort(key=lambda x: (x['date'], x['begin']))

# Format each line for output: date + begin time: character name: data (transcription)
formatted_log_lines = [
    f"{log['date']} {log['begin']}: {log['character']}: {log['data']}" 
    for log in converted_log_lines
]

# Save the formatted log to a new file
output_file = os.path.splitext(args.file)[0] + "_mapped.log"
with open(output_file, "w", encoding="utf-8") as f:
    for line in formatted_log_lines:
        f.write(line + "\n")

# Print the output file path
print(f"Formatted log file saved to: {output_file}")