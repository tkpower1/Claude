"""Text analysis and transformation superpowers."""

import re
from collections import Counter
from typing import Dict, List, Tuple


class TextPowers:
    """A collection of text analysis and transformation utilities."""

    @staticmethod
    def word_frequency(text: str, top_n: int = 10) -> List[Tuple[str, int]]:
        """
        Analyze word frequency in text.

        Args:
            text: The text to analyze
            top_n: Number of top words to return

        Returns:
            List of (word, count) tuples sorted by frequency
        """
        words = re.findall(r'\b[a-zA-Z]+\b', text.lower())
        counter = Counter(words)
        return counter.most_common(top_n)

    @staticmethod
    def reverse_words(text: str) -> str:
        """Reverse each word in the text while maintaining word order."""
        words = text.split()
        return ' '.join(word[::-1] for word in words)

    @staticmethod
    def to_leetspeak(text: str) -> str:
        """Convert text to leetspeak (1337 speak)."""
        leetmap = {
            'a': '4', 'e': '3', 'i': '1', 'o': '0',
            's': '5', 't': '7', 'l': '1', 'b': '8'
        }
        result = []
        for char in text:
            lower = char.lower()
            if lower in leetmap:
                replacement = leetmap[lower]
                result.append(replacement if char.islower() else replacement.upper())
            else:
                result.append(char)
        return ''.join(result)

    @staticmethod
    def analyze_text(text: str) -> Dict[str, any]:
        """
        Perform comprehensive text analysis.

        Returns statistics including character count, word count,
        sentence count, average word length, and more.
        """
        words = re.findall(r'\b[a-zA-Z]+\b', text)
        sentences = re.split(r'[.!?]+', text)
        sentences = [s.strip() for s in sentences if s.strip()]

        char_count = len(text)
        word_count = len(words)
        sentence_count = len(sentences)
        avg_word_length = sum(len(w) for w in words) / word_count if word_count > 0 else 0

        return {
            "character_count": char_count,
            "word_count": word_count,
            "sentence_count": sentence_count,
            "average_word_length": round(avg_word_length, 2),
            "unique_words": len(set(w.lower() for w in words)),
            "longest_word": max(words, key=len) if words else "",
        }

    @staticmethod
    def palindrome_check(text: str) -> bool:
        """Check if text is a palindrome (ignoring spaces and punctuation)."""
        cleaned = re.sub(r'[^a-zA-Z0-9]', '', text.lower())
        return cleaned == cleaned[::-1]

    @staticmethod
    def caesar_cipher(text: str, shift: int = 3) -> str:
        """Apply Caesar cipher encryption to text."""
        result = []
        for char in text:
            if char.isalpha():
                base = ord('A') if char.isupper() else ord('a')
                shifted = (ord(char) - base + shift) % 26 + base
                result.append(chr(shifted))
            else:
                result.append(char)
        return ''.join(result)
