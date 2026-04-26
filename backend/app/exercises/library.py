"""Cognitive exercise library."""

from __future__ import annotations

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Exercise:
    """A single cognitive exercise prompt."""

    name: str
    category: str
    difficulty: str
    prompt_template: str
    follow_up_prompt: str
    scoring_hint: str


EXERCISES: dict[str, list[Exercise]] = {
    "word_association": [
        Exercise(
            "word_association",
            "word_games",
            "easy",
            "I'll say a word, and you say the first thing that comes to mind: sunshine.",
            "After they answer, warmly ask why that word came to mind.",
            "Score higher for a relevant, prompt response; lower if confused or unable to answer.",
        ),
        Exercise(
            "word_association",
            "word_games",
            "easy",
            "I'll say a word, and you say the first thing that comes to mind: garden.",
            "Reflect their answer and gently continue the conversation.",
            "Assess response relevance and ease of retrieval.",
        ),
    ],
    "category_naming": [
        Exercise(
            "category_naming",
            "word_games",
            "medium",
            "Name as many animals as you can in 30 seconds. Ready? Go.",
            "When they pause, praise effort and mention one or two they named.",
            "Score by number of correct category items and whether prompts were needed.",
        ),
        Exercise(
            "category_naming",
            "word_games",
            "medium",
            "Name as many foods as you can in 30 seconds.",
            "Praise effort, not speed, and ask which one they like best.",
            "Score by quantity, category fit, and comfort level.",
        ),
    ],
    "word_chain": [
        Exercise(
            "word_chain",
            "word_games",
            "medium",
            "Let's play a word chain. I say apple; you say a word that starts with E.",
            "If they answer, continue for one more turn; if not, offer an example kindly.",
            "Score by rule-following and ability to continue the chain.",
        )
    ],
    "three_word_recall": [
        Exercise(
            "three_word_recall",
            "memory_recall",
            "easy",
            "I'll say three words: garden, penny, radio. Can you repeat them back?",
            "After they respond, thank them and repeat the words once more if needed.",
            "Score by number of words recalled accurately.",
        ),
        Exercise(
            "three_word_recall",
            "memory_recall",
            "easy",
            "Here are three words: window, coffee, river. Please say them back to me.",
            "If they miss one, reassure them and gently provide it.",
            "Score by immediate recall accuracy without pressure.",
        ),
    ],
    "story_recall": [
        Exercise(
            "story_recall",
            "memory_recall",
            "medium",
            "Listen to this short story: Maria watered her roses, then sat on the porch with tea. Later, her neighbor waved from the sidewalk. What did Maria drink?",
            "Whether correct or not, acknowledge the answer and ask what part they remembered.",
            "Score by recall of key details and comfort with the task.",
        )
    ],
    "daily_event_recall": [
        Exercise(
            "daily_event_recall",
            "memory_recall",
            "easy",
            "What did you have for breakfast today?",
            "Ask one gentle follow-up about whether they enjoyed it.",
            "Score by ability to retrieve a recent event; accept uncertainty calmly.",
        )
    ],
    "general_trivia": [
        Exercise(
            "general_trivia",
            "trivia",
            "easy",
            "Here's a gentle trivia question: what season comes after spring?",
            "Confirm the answer and keep it light; do not quiz repeatedly if they seem tired.",
            "Score by accuracy and confidence.",
        ),
        Exercise(
            "general_trivia",
            "trivia",
            "easy",
            "What do we call the red planet?",
            "If needed, give the answer kindly and move on.",
            "Score by factual recall, not speed.",
        ),
    ],
    "number_games": [
        Exercise(
            "number_games",
            "trivia",
            "medium",
            "Let's do a small number game: what's 15 plus 28?",
            "Praise the effort and avoid pushing if arithmetic feels frustrating.",
            "Score by correctness and whether assistance was needed.",
        ),
        Exercise(
            "number_games",
            "trivia",
            "medium",
            "Try counting backwards from 100 by sevens. Just a few steps is fine.",
            "Stop after a few responses and praise effort.",
            "Score by sequence accuracy and tolerance of the exercise.",
        ),
    ],
    "would_you_rather": [
        Exercise(
            "would_you_rather",
            "conversation_starters",
            "easy",
            "Would you rather sit by the beach or walk in a quiet park? Why?",
            "Reflect their preference and invite a short memory if it feels natural.",
            "Score by engagement and ability to explain a preference.",
        )
    ],
    "this_or_that": [
        Exercise(
            "this_or_that",
            "conversation_starters",
            "easy",
            "This or that: coffee or tea?",
            "Ask a warm follow-up about how they usually like it.",
            "Score by engagement and clear preference expression.",
        )
    ],
    "tell_me_about": [
        Exercise(
            "tell_me_about",
            "conversation_starters",
            "easy",
            "Tell me about one of your favorite childhood foods.",
            "Listen warmly and ask one concrete follow-up.",
            "Score by detail, engagement, and emotional comfort.",
        )
    ],
}

ALIASES = {
    "word_game": ["word_association", "category_naming", "word_chain"],
    "memory_recall": ["three_word_recall", "story_recall", "daily_event_recall"],
    "trivia": ["general_trivia", "number_games"],
    "conversation_starter": ["would_you_rather", "this_or_that", "tell_me_about"],
}


def all_exercises() -> list[Exercise]:
    """Return all exercises."""

    return [exercise for group in EXERCISES.values() for exercise in group]


def get_exercise_types() -> list[str]:
    """Return canonical exercise type names."""

    return sorted(EXERCISES.keys())


def select_exercise(exercise_type: str | None = None) -> Exercise:
    """Pick a random exercise by canonical type or legacy category alias."""

    if not exercise_type:
        return random.choice(all_exercises())
    if exercise_type in EXERCISES:
        return random.choice(EXERCISES[exercise_type])
    if exercise_type in ALIASES:
        return random.choice([exercise for key in ALIASES[exercise_type] for exercise in EXERCISES[key]])
    raise ValueError(f"Unknown exercise type: {exercise_type}")
