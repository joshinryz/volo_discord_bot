import argparse


class CommandLine:
    @staticmethod
    def _optional_int(string):
        return None if string == "None" else int(string)

    @staticmethod
    def _str2bool(string):
        str2val = {"true": True, "false": False}
        if string and string.lower() in str2val:
            return str2val[string.lower()]
        else:
            raise ValueError(
                f"Expected one of {set(str2val.keys())}, got {string}")

    @staticmethod
    def _optional_float(string):
        return None if string == "None" else float(string)

    @classmethod
    def update_from_args(cls, args):
        for key, value in vars(args).items():
            if hasattr(cls, key):
                setattr(cls, key, value)

    @staticmethod
    def read_command_line():
        parser = argparse.ArgumentParser(
            formatter_class=argparse.ArgumentDefaultsHelpFormatter
        )

        parser.add_argument(
            "--verbose",
            type=CommandLine()._str2bool,
            default=False,
            help="Enable verbose logging"
        )

        return parser.parse_args()