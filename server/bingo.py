import asyncio
import json
import os
import random
import wave
from dataclasses import dataclass
from typing import Optional

from loguru import logger
from PIL import Image, ImageDraw, ImageFont
from pipecat.adapters.schemas.function_schema import FunctionSchema
from pipecat.adapters.schemas.tools_schema import ToolsSchema
from pipecat.frames.frames import (
    Frame,
    InterimTranscriptionFrame,
    OutputAudioRawFrame,
    OutputImageRawFrame,
    TranscriptionFrame,
)
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.llm_service import FunctionCallParams, LLMService

from utils import load_file

WORDS = json.loads(load_file("words.json", __file__))


@dataclass
class Player:
    speaker_id: str
    name: str
    score: int = 0


@dataclass
class BingoWord:
    cell: int
    word: str
    value: int
    said: bool = False
    speaker: Optional[Player] = None


class Bingo:

    def __init__(self, output: FrameProcessor):
        # Pipecat
        self.output = output

        # Setup states
        self.players: list[Player] = []
        self.bingo_words: list[BingoWord] = []
        self.sounds: dict[str, OutputAudioRawFrame] = {}
        self.temp_highlight_words: set[str] = set()

        # Load fonts
        script_dir = os.path.dirname(__file__)
        font_path = os.path.join(script_dir, "dejavu.ttf")
        self.word_font = ImageFont.truetype(font_path, 60)
        self.score_font = ImageFont.truetype(font_path, 30)
        self.player_font = ImageFont.truetype(font_path, 45)
        self.player_bold_font = ImageFont.truetype(font_path, 50)
        self.title_font = ImageFont.truetype(font_path, 55)

        # Sounds
        asyncio.create_task(self.load_sounds())

    async def load_sounds(self):
        self.sounds["right"] = await self.load_sound_as_frame("word_right.wav")
        self.sounds["wrong"] = await self.load_sound_as_frame("word_wrong.wav")
        self.sounds["winner"] = await self.load_sound_as_frame("winner.wav")

    async def load_sound_as_frame(self, file_name: str) -> OutputAudioRawFrame:
        full_path = os.path.join(os.path.dirname(__file__), "sounds", file_name)
        with wave.open(full_path) as audio_file:
            return OutputAudioRawFrame(
                audio_file.readframes(-1),
                audio_file.getframerate(),
                audio_file.getnchannels(),
            )

    async def add_player(
        self, params: FunctionCallParams, speaker_id: str, name: str
    ) -> None:
        logger.info(f"New player: {speaker_id} -> {name}")
        self.players.append(Player(speaker_id=speaker_id, name=name))
        logger.info(f"Players: {self.players}")
        await params.result_callback({"result": "player added"})

    async def get_words(self, params: FunctionCallParams) -> None:
        self.bingo_words.extend(
            [
                BingoWord(cell=i + 1, word=word, value=1)
                for i, word in enumerate(random.sample(WORDS["obvious"], 3))
            ]
        )
        self.bingo_words.extend(
            [
                BingoWord(cell=i + 1, word=word, value=2)
                for i, word in enumerate(random.sample(WORDS["hard"], 3))
            ]
        )
        self.bingo_words.extend(
            [
                BingoWord(cell=i + 1, word=word, value=3)
                for i, word in enumerate(random.sample(WORDS["obscure"], 3))
            ]
        )
        random.shuffle(self.bingo_words)

        print("")
        print("Bingo words:")
        for word in self.bingo_words:
            print(f" {word.cell}: {word.word} ({word.value})")
        print("")

        # Show grid first, before the LLM response
        await self.show_word_grid()

        logger.info(f"Bingo words: {self.bingo_words}")
        await params.result_callback(
            {
                "bingo_words": [w.word for w in self.bingo_words],
                "result": "words are now shown on screen.",
            }
        )

    async def word_spoken(
        self, params: FunctionCallParams, speaker_id: str, word: str, valid_use: bool
    ) -> None:
        bingo_word = next(
            (w for w in self.bingo_words if w.word.lower() == word.lower()), None
        )
        speaker = next(
            (player for player in self.players if player.speaker_id == speaker_id), None
        )

        logger.info(f"Word spoken: {speaker_id} -> `{word}` (valid use: {valid_use})")
        logger.debug(f"Bingo word: {bingo_word}")
        logger.debug(f"Speaker: {speaker}")

        if bingo_word is None or speaker is None:
            await params.result_callback(None)
            return

        if not valid_use:
            await self.play_sound("wrong")
            await params.result_callback(None)
            return

        await self.play_sound("right")

        bingo_word.said = True
        bingo_word.speaker = speaker
        speaker.score += bingo_word.value

        # Clear any temporary highlight for this word now that it has been confirmed.
        try:
            self.temp_highlight_words.discard(bingo_word.word.lower())
        except Exception:
            pass

        # Update grid first, before the LLM response
        await self.show_word_grid()

        winner: Player = None

        if all(bingo_word.said for bingo_word in self.bingo_words):
            logger.info("All words have been spoken!")
            winner = max(self.players, key=lambda p: p.score)

        elif speaker.score >= 10:
            logger.info(f"{speaker.name} has reached 10 points!")
            winner = speaker

        if winner is not None:
            await self.play_sound("winner")
            await params.result_callback(
                {
                    "winner": speaker.speaker_id,
                    "name": speaker.name,
                    "score": speaker.score,
                }
            )
            return

        logger.debug(f"Player Updated: {speaker}")
        logger.debug(f"Bingo Word Updated: {bingo_word}")

        await params.result_callback(None)

    async def play_sound(self, sound: str) -> None:
        if sound in self.sounds:
            await self.output.queue_frame(self.sounds[sound])

    async def no_word_spoken(self, params: FunctionCallParams) -> None:
        await params.result_callback(None)

    async def start_over(self, params: FunctionCallParams) -> None:
        for player in self.players:
            player.score = 0
        self.bingo_words.clear()
        await self.get_words(params)

    async def show_word_grid(self) -> None:
        # Create a new image with a white background
        img = Image.new("RGB", (1920, 1080), color="white")
        draw = ImageDraw.Draw(img)

        # Grid settings
        grid_size = 3
        cell_width = 450
        cell_height = 300
        grid_width = grid_size * cell_width
        grid_height = grid_size * cell_height

        # Position grid to the left to make room for scoreboard
        start_x = 100
        start_y = (img.height - grid_height) // 2

        # Define colours
        colours = {
            1: (255, 255, 255),  # white for score 1
            2: (173, 216, 230),  # light blue for score 2
            3: (144, 238, 144),  # light green for score 3
        }
        said_colour = (128, 0, 128)  # purple for said words

        # Draw the grid
        for i, bingo_word in enumerate(self.bingo_words):
            row = i // grid_size
            col = i % grid_size

            # Calculate cell position
            x = start_x + col * cell_width
            y = start_y + row * cell_height

            # Determine cell background colour
            if bingo_word.said:
                bg_colour = said_colour
                text_colour = (255, 255, 255)  # white text for said words
            else:
                bg_colour = colours[bingo_word.value]
                text_colour = (0, 0, 0)  # black text for unsaid words

            # Draw cell background
            # If a word is being heard in interim speech, highlight with a yellow border for immediate feedback
            is_temp_highlight = (not bingo_word.said) and (
                bingo_word.word.lower() in self.temp_highlight_words
            )
            border_colour = (255, 215, 0) if is_temp_highlight else (0, 0, 0)
            border_width = 6 if is_temp_highlight else 3
            draw.rectangle(
                [x, y, x + cell_width, y + cell_height],
                fill=bg_colour,
                outline=border_colour,
                width=border_width,
            )

            # Draw the word (centred in cell)
            word_bbox = draw.textbbox((0, 0), bingo_word.word, font=self.word_font)
            word_width = word_bbox[2] - word_bbox[0]
            word_height = word_bbox[3] - word_bbox[1]
            word_x = x + (cell_width - word_width) // 2
            word_y = y + (cell_height - word_height) // 2 - 20  # Slightly above centre
            draw.text(
                (word_x, word_y), bingo_word.word, font=self.word_font, fill=text_colour
            )

            # Draw the score in lower-right corner
            score_text = str(bingo_word.value)
            score_bbox = draw.textbbox((0, 0), score_text, font=self.score_font)
            score_width = score_bbox[2] - score_bbox[0]
            score_height = score_bbox[3] - score_bbox[1]
            score_x = x + cell_width - score_width - 25
            score_y = y + cell_height - score_height - 25
            draw.text(
                (score_x, score_y), score_text, font=self.score_font, fill=text_colour
            )

        # Draw player scoreboard on the right
        scoreboard_x = start_x + grid_width + 50
        scoreboard_y = start_y + 30

        # Title for scoreboard with fancy styling
        title_text = "PLAYERS"
        # Draw shadow for title
        draw.text(
            (scoreboard_x + 3, scoreboard_y + 3),
            title_text,
            font=self.title_font,
            fill=(128, 128, 128),
        )
        # Draw main title
        draw.text(
            (scoreboard_x, scoreboard_y),
            title_text,
            font=self.title_font,
            fill=(0, 0, 139),
        )  # Dark blue
        scoreboard_y += 80

        # Determine if there's a winner
        winner = None
        if all(bingo_word.said for bingo_word in self.bingo_words):
            winner = max(self.players, key=lambda p: p.score) if self.players else None
        else:
            winner = next((p for p in self.players if p.score >= 10), None)

        # Draw each player's score with fancy styling
        for i, player in enumerate(self.players):
            is_winner = player == winner
            font_to_use = self.player_bold_font if is_winner else self.player_font

            # Create fancy player text without emojis
            if is_winner:
                player_text = f"{player.name.upper()}: {player.score}"
                # Draw gold shadow for winner
                draw.text(
                    (scoreboard_x + 2, scoreboard_y + 2),
                    player_text,
                    font=font_to_use,
                    fill=(255, 215, 0),
                )
                # Draw winner in red with gold outline effect
                colour = (220, 20, 60)  # Crimson red
            else:
                player_text = f"{player.name}: {player.score}"
                # Draw subtle shadow for regular players
                draw.text(
                    (scoreboard_x + 2, scoreboard_y + 2),
                    player_text,
                    font=font_to_use,
                    fill=(200, 200, 200),
                )
                # Alternate colours for players
                colours_list = [
                    (0, 100, 0),
                    (0, 0, 139),
                    (139, 0, 139),
                    (255, 140, 0),
                ]  # Green, dark blue, dark magenta, orange
                colour = colours_list[i % len(colours_list)]

            # Draw main text
            draw.text(
                (scoreboard_x, scoreboard_y),
                player_text,
                font=font_to_use,
                fill=colour,
            )
            scoreboard_y += 70

        # Send the data
        await self.output.queue_frame(
            OutputImageRawFrame(
                image=img.tobytes(),
                size=(1920, 1080),
                format="RGB",
            )
        )

    async def splash_screen(self) -> None:
        # Create a new image with a white background
        img = Image.new("RGB", (1920, 1080), color="white")
        draw = ImageDraw.Draw(img)

        # Load a font
        script_dir = os.path.dirname(__file__)
        font_path = os.path.join(script_dir, "dejavu.ttf")
        font = ImageFont.truetype(font_path, 100)

        # Calculate the size of the text
        bbox = draw.textbbox((0, 0), "Bullshit Bingo!", font=font)
        text_width = bbox[2] - bbox[0]
        text_height = bbox[3] - bbox[1]

        # Draw the text on the image
        x = (img.width - text_width) // 2
        y = (img.height - text_height) // 2
        draw.text((x, y), "Bullshit Bingo!", font=font, fill="black")

        # Debug
        logger.info("BINGO: Splash screen sent")

        # Send the data
        await self.output.queue_frame(
            OutputImageRawFrame(
                image=img.tobytes(),
                size=(1920, 1080),
                format="RGB",
            )
        )

    def register_functions(self, llm: LLMService) -> list[ToolsSchema]:

        # Tool definitions
        schema: list[FunctionSchema] = []

        # Add a player to the game
        llm.register_direct_function(self.add_player)
        schema.append(
            FunctionSchema(
                name="add_player",
                description="Use this tool to add a player to the game.",
                properties={
                    "speaker_id": {
                        "type": "string",
                        "description": "The speaker ID of the player.",
                    },
                    "name": {
                        "type": "string",
                        "description": "The first name of the player.",
                    },
                },
                required=[
                    "speaker_id",
                    "name",
                ],
            )
        )

        # Get game words
        llm.register_direct_function(self.get_words)
        schema.append(
            FunctionSchema(
                name="get_words",
                description="Use this tool to get the words for the game.",
                properties={},
                required=[],
            )
        )

        # Extract words spoken
        llm.register_direct_function(self.word_spoken)
        schema.append(
            FunctionSchema(
                name="word_spoken",
                description="Use this tool to record a word from the bingo list that was spoken. Accept misspelled words as valid. Only report words that are in the list.",
                properties={
                    "speaker_id": {
                        "type": "string",
                        "description": "The speaker ID of the player.",
                    },
                    "word": {
                        "type": "string",
                        "description": "The word that was spoken. Must be in the list.",
                    },
                    "valid_use": {
                        "type": "boolean",
                        "description": "Whether the word was used in a valid way (as part of a conversation and not as a statement).",
                    },
                },
                required=[
                    "speaker_id",
                    "word",
                    "valid_use",
                ],
            )
        )

        # No words spoken
        llm.register_direct_function(self.no_word_spoken)
        schema.append(
            FunctionSchema(
                name="no_word_spoken",
                description="Use this tool when no bingo words has been spoken or you are not engaged in conversation.",
                properties={},
                required=[],
            )
        )

        # Start the game again
        llm.register_direct_function(self.start_over)
        schema.append(
            FunctionSchema(
                name="start_over",
                description="Use this tool to start a new game.",
                properties={},
                required=[],
            )
        )

        # Return the tool schema
        return ToolsSchema(standard_tools=schema)


class WordFinder(FrameProcessor):

    def __init__(self, bingo: Bingo):
        super().__init__()
        self.bingo = bingo

    async def process_frame(self, frame: Frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)

        if isinstance(frame, (InterimTranscriptionFrame, TranscriptionFrame)):
            logger.debug(f"{frame.text}")

            if frame.text and isinstance(frame.text, str):
                text = frame.text.lower()
                bingo_words_found = [
                    word
                    for word in self.bingo.bingo_words
                    if (not word.said) and (word.word.lower() in text)
                ]
                if bingo_words_found:
                    logger.warning(
                        f"Frame contains the following bingo words: {bingo_words_found}"
                    )

                    # Update temporary highlights with words heard in interim/final speech.
                    self.bingo.temp_highlight_words.clear()
                    for bw in bingo_words_found:
                        self.bingo.temp_highlight_words.add(bw.word.lower())

                    # Trigger a non-blocking grid refresh so the video updates while TTS is speaking.
                    asyncio.create_task(self.bingo.show_word_grid())

                # Clear highlights if no words are currently being heard.
                elif self.bingo.temp_highlight_words:
                    self.bingo.temp_highlight_words.clear()
                    asyncio.create_task(self.bingo.show_word_grid())

        await self.push_frame(frame, direction)
