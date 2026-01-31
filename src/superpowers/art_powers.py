"""ASCII art generation superpowers."""

from typing import List


class ArtPowers:
    """A collection of ASCII art generation utilities."""

    # Pre-defined ASCII art patterns
    PATTERNS = {
        "star": [
            "    *    ",
            "   ***   ",
            "  *****  ",
            " ******* ",
            "*********",
            " ******* ",
            "  *****  ",
            "   ***   ",
            "    *    ",
        ],
        "heart": [
            "  **   **  ",
            " **** **** ",
            "***********",
            " ********* ",
            "  *******  ",
            "   *****   ",
            "    ***    ",
            "     *     ",
        ],
        "rocket": [
            "     /\\     ",
            "    /  \\    ",
            "   /    \\   ",
            "  |  ()  |  ",
            "  |      |  ",
            "  |      |  ",
            " /|      |\\ ",
            "/_|______|_\\",
            "   |    |   ",
            "  /|    |\\  ",
            " / |    | \\ ",
            "/__|    |__\\",
        ],
        "lightning": [
            "     **",
            "    ** ",
            "   **  ",
            "  **   ",
            " ******",
            "   **  ",
            "  **   ",
            " **    ",
            "**     ",
        ],
    }

    @staticmethod
    def get_pattern(name: str) -> str:
        """Get a pre-defined ASCII art pattern."""
        if name not in ArtPowers.PATTERNS:
            available = ", ".join(ArtPowers.PATTERNS.keys())
            raise ValueError(f"Unknown pattern: {name}. Available: {available}")
        return "\n".join(ArtPowers.PATTERNS[name])

    @staticmethod
    def list_patterns() -> List[str]:
        """List all available pattern names."""
        return list(ArtPowers.PATTERNS.keys())

    @staticmethod
    def banner(text: str, char: str = "*", padding: int = 2) -> str:
        """
        Create a banner around text.

        Args:
            text: Text to display in banner
            char: Character to use for border
            padding: Padding around text

        Returns:
            ASCII banner string
        """
        width = len(text) + (padding * 2) + 2
        border = char * width
        spaces = " " * padding

        lines = [
            border,
            f"{char}{spaces}{text}{spaces}{char}",
            border,
        ]
        return "\n".join(lines)

    @staticmethod
    def box(text: str, style: str = "single") -> str:
        """
        Create a box around text.

        Args:
            text: Text to put in box (can be multiline)
            style: Box style ('single', 'double', 'rounded')

        Returns:
            Boxed text string
        """
        styles = {
            "single": {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "-", "v": "|"},
            "double": {"tl": "+", "tr": "+", "bl": "+", "br": "+", "h": "=", "v": "║"},
            "rounded": {"tl": "/", "tr": "\\", "bl": "\\", "br": "/", "h": "-", "v": "|"},
        }

        if style not in styles:
            style = "single"

        s = styles[style]
        lines = text.split("\n")
        max_width = max(len(line) for line in lines)

        result = [s["tl"] + s["h"] * (max_width + 2) + s["tr"]]
        for line in lines:
            padded = line.ljust(max_width)
            result.append(f"{s['v']} {padded} {s['v']}")
        result.append(s["bl"] + s["h"] * (max_width + 2) + s["br"])

        return "\n".join(result)

    @staticmethod
    def big_text(text: str) -> str:
        """
        Convert text to big ASCII letters.

        Supports uppercase letters A-Z and space.
        """
        letters = {
            'A': ["  A  ", " A A ", "AAAAA", "A   A", "A   A"],
            'B': ["BBBB ", "B   B", "BBBB ", "B   B", "BBBB "],
            'C': [" CCC ", "C    ", "C    ", "C    ", " CCC "],
            'D': ["DDD  ", "D  D ", "D   D", "D  D ", "DDD  "],
            'E': ["EEEEE", "E    ", "EEE  ", "E    ", "EEEEE"],
            'F': ["FFFFF", "F    ", "FFF  ", "F    ", "F    "],
            'G': [" GGG ", "G    ", "G  GG", "G   G", " GGG "],
            'H': ["H   H", "H   H", "HHHHH", "H   H", "H   H"],
            'I': ["IIIII", "  I  ", "  I  ", "  I  ", "IIIII"],
            'J': ["JJJJJ", "   J ", "   J ", "J  J ", " JJ  "],
            'K': ["K   K", "K  K ", "KKK  ", "K  K ", "K   K"],
            'L': ["L    ", "L    ", "L    ", "L    ", "LLLLL"],
            'M': ["M   M", "MM MM", "M M M", "M   M", "M   M"],
            'N': ["N   N", "NN  N", "N N N", "N  NN", "N   N"],
            'O': [" OOO ", "O   O", "O   O", "O   O", " OOO "],
            'P': ["PPPP ", "P   P", "PPPP ", "P    ", "P    "],
            'Q': [" QQQ ", "Q   Q", "Q   Q", "Q  Q ", " QQ Q"],
            'R': ["RRRR ", "R   R", "RRRR ", "R  R ", "R   R"],
            'S': [" SSS ", "S    ", " SSS ", "    S", " SSS "],
            'T': ["TTTTT", "  T  ", "  T  ", "  T  ", "  T  "],
            'U': ["U   U", "U   U", "U   U", "U   U", " UUU "],
            'V': ["V   V", "V   V", "V   V", " V V ", "  V  "],
            'W': ["W   W", "W   W", "W W W", "WW WW", "W   W"],
            'X': ["X   X", " X X ", "  X  ", " X X ", "X   X"],
            'Y': ["Y   Y", " Y Y ", "  Y  ", "  Y  ", "  Y  "],
            'Z': ["ZZZZZ", "   Z ", "  Z  ", " Z   ", "ZZZZZ"],
            ' ': ["     ", "     ", "     ", "     ", "     "],
        }

        text = text.upper()
        result_lines = ["", "", "", "", ""]

        for char in text:
            if char in letters:
                for i, line in enumerate(letters[char]):
                    result_lines[i] += line + " "
            else:
                for i in range(5):
                    result_lines[i] += "     " + " "

        return "\n".join(result_lines)

    @staticmethod
    def progress_bar(
        current: int,
        total: int,
        width: int = 40,
        filled_char: str = "█",
        empty_char: str = "░"
    ) -> str:
        """
        Generate an ASCII progress bar.

        Args:
            current: Current progress value
            total: Total/maximum value
            width: Width of the progress bar
            filled_char: Character for filled portion
            empty_char: Character for empty portion

        Returns:
            Progress bar string with percentage
        """
        if total <= 0:
            total = 1
        percentage = min(current / total, 1.0)
        filled_width = int(width * percentage)
        empty_width = width - filled_width

        bar = filled_char * filled_width + empty_char * empty_width
        percent_text = f"{percentage * 100:.1f}%"

        return f"[{bar}] {percent_text}"
