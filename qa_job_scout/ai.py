import json
import os

from openai import OpenAI

from .core import Vacancy, deterministic_letter


def enrich(vacancy: Vacancy, profile: dict) -> Vacancy:
    """Use AI only after deterministic eligibility checks; never make up candidate facts."""
    if not os.getenv("OPENAI_API_KEY") or vacancy.status != "recommended":
        vacancy.cover_letter = deterministic_letter(vacancy, profile)
        return vacancy
    prompt = f"""Ты карьерный ассистент. Напиши короткое сопроводительное письмо на русском.
Используй ТОЛЬКО факты из профиля. Не утверждай знание английского и не обещай опыт
автоматизации, кроме явно указанного. Не упоминай оценку вакансии.

Профиль: {json.dumps(profile, ensure_ascii=False)}
Вакансия: {vacancy.title}\n{vacancy.text[:6000]}
"""
    try:
        client = OpenAI()
        response = client.responses.create(
            model=os.getenv("OPENAI_MODEL", "gpt-4.1-mini"),
            input=prompt,
        )
        vacancy.cover_letter = response.output_text.strip()
    except Exception as exc:  # noqa: BLE001
        print(f"AI недоступен, использован шаблон: {type(exc).__name__}")
        vacancy.cover_letter = deterministic_letter(vacancy, profile)
    return vacancy
