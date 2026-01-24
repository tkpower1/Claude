#!/usr/bin/env python3
"""
Superpowers CLI - A toolkit demonstrating various capabilities.

Usage:
    superpowers text analyze <text>
    superpowers text frequency <text> [--top N]
    superpowers text reverse <text>
    superpowers text leet <text>
    superpowers text cipher <text> [--shift N]
    superpowers code class <name> --attrs ATTRS [--methods METHODS] [--lang LANG]
    superpowers code function <name> --params PARAMS [--lang LANG]
    superpowers data flatten <json>
    superpowers data transform-keys <json> [--case CASE]
    superpowers art pattern <name>
    superpowers art banner <text> [--char CHAR]
    superpowers art box <text> [--style STYLE]
    superpowers art big <text>
    superpowers art progress <current> <total>
"""

import argparse
import json
import sys
from typing import List, Optional

from . import TextPowers, CodePowers, DataPowers, ArtPowers, __version__


def create_parser() -> argparse.ArgumentParser:
    """Create the argument parser with all subcommands."""
    parser = argparse.ArgumentParser(
        prog="superpowers",
        description="A CLI toolkit demonstrating Claude's superpowers",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--version", "-v",
        action="version",
        version=f"superpowers {__version__}"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Text commands
    text_parser = subparsers.add_parser("text", help="Text analysis and transformation")
    text_sub = text_parser.add_subparsers(dest="subcommand")

    # text analyze
    analyze = text_sub.add_parser("analyze", help="Analyze text statistics")
    analyze.add_argument("text", help="Text to analyze")

    # text frequency
    freq = text_sub.add_parser("frequency", help="Word frequency analysis")
    freq.add_argument("text", help="Text to analyze")
    freq.add_argument("--top", "-n", type=int, default=10, help="Number of top words")

    # text reverse
    reverse = text_sub.add_parser("reverse", help="Reverse words in text")
    reverse.add_argument("text", help="Text to reverse")

    # text leet
    leet = text_sub.add_parser("leet", help="Convert to leetspeak")
    leet.add_argument("text", help="Text to convert")

    # text cipher
    cipher = text_sub.add_parser("cipher", help="Caesar cipher encryption")
    cipher.add_argument("text", help="Text to encrypt")
    cipher.add_argument("--shift", "-s", type=int, default=3, help="Shift amount")

    # text palindrome
    palindrome = text_sub.add_parser("palindrome", help="Check if text is palindrome")
    palindrome.add_argument("text", help="Text to check")

    # Code commands
    code_parser = subparsers.add_parser("code", help="Code generation utilities")
    code_sub = code_parser.add_subparsers(dest="subcommand")

    # code class
    cls = code_sub.add_parser("class", help="Generate a class skeleton")
    cls.add_argument("name", help="Class name")
    cls.add_argument("--attrs", "-a", required=True, help="Comma-separated attributes")
    cls.add_argument("--methods", "-m", default="", help="Comma-separated methods")
    cls.add_argument("--lang", "-l", default="python",
                     choices=["python", "javascript", "typescript"],
                     help="Target language")

    # code function
    func = code_sub.add_parser("function", help="Generate a function skeleton")
    func.add_argument("name", help="Function name")
    func.add_argument("--params", "-p", required=True, help="Comma-separated parameters")
    func.add_argument("--lang", "-l", default="python",
                      choices=["python", "javascript", "typescript"],
                      help="Target language")

    # code count
    count = code_sub.add_parser("count", help="Count lines of code")
    count.add_argument("file", help="File to analyze (use - for stdin)")

    # Data commands
    data_parser = subparsers.add_parser("data", help="Data manipulation utilities")
    data_sub = data_parser.add_subparsers(dest="subcommand")

    # data flatten
    flatten = data_sub.add_parser("flatten", help="Flatten nested JSON")
    flatten.add_argument("json_data", help="JSON string to flatten")

    # data unflatten
    unflatten = data_sub.add_parser("unflatten", help="Unflatten JSON")
    unflatten.add_argument("json_data", help="JSON string to unflatten")

    # data transform-keys
    transform = data_sub.add_parser("transform-keys", help="Transform JSON keys")
    transform.add_argument("json_data", help="JSON string")
    transform.add_argument("--case", "-c", default="snake_case",
                          choices=["snake_case", "camelCase", "PascalCase"],
                          help="Target case style")

    # data json-to-csv
    j2c = data_sub.add_parser("json-to-csv", help="Convert JSON array to CSV")
    j2c.add_argument("json_data", help="JSON array string")

    # Art commands
    art_parser = subparsers.add_parser("art", help="ASCII art generation")
    art_sub = art_parser.add_subparsers(dest="subcommand")

    # art pattern
    pattern = art_sub.add_parser("pattern", help="Display ASCII art pattern")
    pattern.add_argument("name", help="Pattern name (star, heart, rocket, lightning)")

    # art list
    art_sub.add_parser("list", help="List available patterns")

    # art banner
    banner = art_sub.add_parser("banner", help="Create text banner")
    banner.add_argument("text", help="Text for banner")
    banner.add_argument("--char", "-c", default="*", help="Border character")

    # art box
    box = art_sub.add_parser("box", help="Create text box")
    box.add_argument("text", help="Text to put in box")
    box.add_argument("--style", "-s", default="single",
                     choices=["single", "double", "rounded"],
                     help="Box style")

    # art big
    big = art_sub.add_parser("big", help="Convert text to big ASCII letters")
    big.add_argument("text", help="Text to convert (A-Z and space)")

    # art progress
    progress = art_sub.add_parser("progress", help="Generate progress bar")
    progress.add_argument("current", type=int, help="Current value")
    progress.add_argument("total", type=int, help="Total value")
    progress.add_argument("--width", "-w", type=int, default=40, help="Bar width")

    return parser


def handle_text(args: argparse.Namespace) -> str:
    """Handle text subcommands."""
    if args.subcommand == "analyze":
        result = TextPowers.analyze_text(args.text)
        return json.dumps(result, indent=2)
    elif args.subcommand == "frequency":
        result = TextPowers.word_frequency(args.text, args.top)
        return "\n".join(f"{word}: {count}" for word, count in result)
    elif args.subcommand == "reverse":
        return TextPowers.reverse_words(args.text)
    elif args.subcommand == "leet":
        return TextPowers.to_leetspeak(args.text)
    elif args.subcommand == "cipher":
        return TextPowers.caesar_cipher(args.text, args.shift)
    elif args.subcommand == "palindrome":
        is_palindrome = TextPowers.palindrome_check(args.text)
        return f"'{args.text}' is {'a palindrome' if is_palindrome else 'not a palindrome'}"
    else:
        return "Unknown text subcommand. Use --help for usage."


def handle_code(args: argparse.Namespace) -> str:
    """Handle code subcommands."""
    if args.subcommand == "class":
        attrs = [a.strip() for a in args.attrs.split(",") if a.strip()]
        methods = [m.strip() for m in args.methods.split(",") if m.strip()] if args.methods else []
        return CodePowers.generate_class(args.name, attrs, methods, args.lang)
    elif args.subcommand == "function":
        params = [p.strip() for p in args.params.split(",") if p.strip()]
        return CodePowers.generate_function(args.name, params, language=args.lang)
    elif args.subcommand == "count":
        if args.file == "-":
            content = sys.stdin.read()
        else:
            with open(args.file, "r") as f:
                content = f.read()
        result = CodePowers.count_lines(content)
        return json.dumps(result, indent=2)
    else:
        return "Unknown code subcommand. Use --help for usage."


def handle_data(args: argparse.Namespace) -> str:
    """Handle data subcommands."""
    if args.subcommand == "flatten":
        data = json.loads(args.json_data)
        result = DataPowers.flatten_dict(data)
        return json.dumps(result, indent=2)
    elif args.subcommand == "unflatten":
        data = json.loads(args.json_data)
        result = DataPowers.unflatten_dict(data)
        return json.dumps(result, indent=2)
    elif args.subcommand == "transform-keys":
        data = json.loads(args.json_data)
        result = DataPowers.transform_keys(data, args.case)
        return json.dumps(result, indent=2)
    elif args.subcommand == "json-to-csv":
        data = json.loads(args.json_data)
        return DataPowers.json_to_csv(data)
    else:
        return "Unknown data subcommand. Use --help for usage."


def handle_art(args: argparse.Namespace) -> str:
    """Handle art subcommands."""
    if args.subcommand == "pattern":
        return ArtPowers.get_pattern(args.name)
    elif args.subcommand == "list":
        patterns = ArtPowers.list_patterns()
        return "Available patterns: " + ", ".join(patterns)
    elif args.subcommand == "banner":
        return ArtPowers.banner(args.text, args.char)
    elif args.subcommand == "box":
        return ArtPowers.box(args.text, args.style)
    elif args.subcommand == "big":
        return ArtPowers.big_text(args.text)
    elif args.subcommand == "progress":
        return ArtPowers.progress_bar(args.current, args.total, args.width)
    else:
        return "Unknown art subcommand. Use --help for usage."


def main(argv: Optional[List[str]] = None) -> int:
    """Main entry point for the CLI."""
    parser = create_parser()
    args = parser.parse_args(argv)

    if not args.command:
        parser.print_help()
        return 0

    try:
        if args.command == "text":
            if not args.subcommand:
                print("Error: text command requires a subcommand")
                return 1
            print(handle_text(args))
        elif args.command == "code":
            if not args.subcommand:
                print("Error: code command requires a subcommand")
                return 1
            print(handle_code(args))
        elif args.command == "data":
            if not args.subcommand:
                print("Error: data command requires a subcommand")
                return 1
            print(handle_data(args))
        elif args.command == "art":
            if not args.subcommand:
                print("Error: art command requires a subcommand")
                return 1
            print(handle_art(args))
        else:
            parser.print_help()
            return 1
    except json.JSONDecodeError as e:
        print(f"Error: Invalid JSON - {e}", file=sys.stderr)
        return 1
    except ValueError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    except FileNotFoundError as e:
        print(f"Error: File not found - {e}", file=sys.stderr)
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
