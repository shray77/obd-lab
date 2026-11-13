"""Вердикт по статус-байту.

Ключевая идея инструмента: решение "стирать или нет" принимается
по механике статуса, а не по названию кода. Здесь нет базы знаний —
только логика. База знаний добавляет "что проверять".
"""

from __future__ import annotations

from dataclasses import dataclass

from .uds import BIT

LEVEL_LABELS = {
    "clear_safe": "МОЖНО СТИРАТЬ",
    "do_not_clear": "НЕ СТИРАТЬ КАК ЛОЖНУЮ",
    "confirmed": "ПОДТВЕРЖДЕНА, НЕ ЛОЖНАЯ",
    "monitor": "НАБЛЮДАТЬ",
    "blocked": "СТЕРЕТЬ НЕВОЗМОЖНО",
}


@dataclass(frozen=True)
class Verdict:
    level: str
    headline: str
    reason: str
    action: str

    @property
    def label(self) -> str:
        return LEVEL_LABELS.get(self.level, self.level)

    def render(self) -> str:
        return f"[{self.label}] {self.headline}\n  Почему: {self.reason}\n  Что делать: {self.action}"


def verdict(status: int, permanent: bool = False) -> Verdict:
    if permanent:
        return Verdict(
            "blocked",
            "Permanent DTC",
            "Такой код не стирается ни одним сканером, включая дилерский. "
            "Он уходит сам, когда монитор несколько циклов подряд проходит без сбоя.",
            "Не тратить время на стирание: либо устранить причину и ждать, "
            "либо продолжать наблюдение.",
        )

    if status & BIT["testFailed"] or status & BIT["testFailedThisOperationCycle"]:
        return Verdict(
            "do_not_clear",
            "Неисправность активна прямо сейчас",
            "Монитор не проходит в этом цикле зажигания. Стирание уберёт лампу, "
            "но код вернётся на следующем пуске — причина никуда не делась.",
            "Искать причину по списку проверок, а не стирать.",
        )

    if status & BIT["warningIndicatorRequested"] or status & BIT["confirmedDTC"]:
        return Verdict(
            "confirmed",
            "Подтверждённая, но сейчас не активна",
            "Сбой был подтверждён ЭБУ, в этом цикле не повторялся. "
            "Стирание возможно, но если причина осталась — код вернётся.",
            "Сначала проверить условия из freeze frame. Стирать только "
            "если неисправность уже устранена или это разовая помеха.",
        )

    if status & BIT["pendingDTC"]:
        return Verdict(
            "monitor",
            "Ожидает подтверждения",
            "Сбой зафиксирован один раз, ЭБУ ждёт повторения. "
            "Часто это начало плавающей неисправности.",
            "Не стирать: дать 2–3 цикла и посмотреть журнал — "
            "повторится ли при тех же условиях.",
        )

    if status & BIT["testFailedSinceLastClear"]:
        return Verdict(
            "clear_safe",
            "Историческая, сейчас чисто",
            "Сбой был после последнего сброса, но монитор давно проходит "
            "и лампа не запрашивается.",
            "Стирать безопасно. Если вернётся — значит причина осталась.",
        )

    if status & (BIT["testNotCompletedThisOperationCycle"] | BIT["testNotCompletedSinceLastClear"]):
        return Verdict(
            "monitor",
            "Монитор ещё не отработал",
            "ЭБУ пока не проверил эту систему в текущих условиях.",
            "Дать машине отработать цикл (обычно поездка 10–20 минут) и перечитать.",
        )

    return Verdict(
        "monitor",
        "Нет активных признаков",
        "Статус пустой: код есть в памяти, но ни один бит не выставлен.",
        "Наблюдать. Обычно такие записи безопасно стирать.",
    )
