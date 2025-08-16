from src.utils.commandline import CommandLine


class CLIArgs(CommandLine):
    verbose = False
    # None means: resolve from environment or fall back to default
    transcriber_type = None