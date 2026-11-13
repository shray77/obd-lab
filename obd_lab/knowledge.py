"""База знаний: что означает код и что с ним делать.

Два слоя:
  * курируемый русский (data/dtc/*.yaml) — наш, с проверками и приоритетами;
  * справочный OBDex (data/obdex.sqlite3, CC0) — 9 533 общих кода, англ.

Сначала ищется русская запись, потом справочник. Вердикт по статус-байту
считается отдельно (verdict.py) и от базы знаний не зависит.
"""

from __future__ import annotations

from pathlib import Path

import yaml

from .reference import ReferenceBase, ReferenceEntry

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "dtc"

WEIGHT_RU = {"high": "частая причина", "medium": "возможная причина", "low": "редкая причина"}
LIKELIHOOD_RU = {"high": "высокая", "medium": "средняя", "low": "низкая"}
DIFFICULTY_RU = {"easy": "просто", "medium": "средне", "shop_only": "только сервис"}


class KnowledgeBase:
    def __init__(
        self,
        path: str | Path | None = None,
        reference: ReferenceBase | None = None,
    ) -> None:
        self.path = Path(path) if path else DEFAULT_PATH
        self.codes: dict[str, dict] = {}
        self._load(self.path)
        self.reference = reference if reference is not None else ReferenceBase()

    def _load(self, path: Path) -> None:
        files = sorted(path.glob("*.yaml")) if path.is_dir() else [path]
        for file in files:
            raw = yaml.safe_load(file.read_text(encoding="utf-8")) or {}
            for code, entry in (raw.get("codes") or {}).items():
                self.codes[code.upper()] = entry

    def get(self, code: str) -> dict | None:
        return self.codes.get(code.upper())

    def explain(self, code: str) -> str:
        entry = self.get(code)
        if entry is not None:
            return self._render_curated(code.upper(), entry)

        reference = self.reference.get(code)
        if reference is not None:
            return self._render_reference(reference)

        if self.reference.available:
            return (
                f"{code.upper()}: нет ни в русской базе, ни в справочнике OBDex. "
                "Статус-байт и freeze frame всё равно информативны."
            )
        return (
            f"{code.upper()}: описания нет. Справочник OBDex не подключён — "
            "см. tools/import_obdex.py."
        )

    def _render_curated(self, code: str, entry: dict) -> str:
        lines = [f"{code} — {entry.get('title', 'без названия')}"]
        if entry.get("safety_critical"):
            lines.append(
                "  !! СИСТЕМА БЕЗОПАСНОСТИ: не стирать вслепую и не проверять "
                "цепь самому, если нет квалификации."
            )
        systems = entry.get("systems") or []
        if systems:
            lines.append(f"  Системы: {', '.join(systems)}")
        if entry.get("meaning"):
            lines.append(f"  Смысл: {_one_line(entry['meaning'])}")
        if entry.get("symptoms"):
            lines.append(f"  Симптомы: {', '.join(entry['symptoms'])}")
        if entry.get("monitor_hint"):
            lines.append(f"  Как проверить данными: {_one_line(entry['monitor_hint'])}")

        causes = entry.get("causes") or []
        if causes:
            lines.append("  Причины и проверки:")
            for cause in causes:
                weight = WEIGHT_RU.get(cause.get("weight", ""), cause.get("weight", ""))
                head = f"    - {cause.get('title', 'причина')}"
                if weight:
                    head += f" ({weight})"
                if cause.get("part"):
                    head += f", деталь: {cause['part']}"
                lines.append(head)
                if cause.get("check"):
                    lines.append(f"      проверка: {_one_line(cause['check'])}")

        for note in entry.get("notes") or []:
            lines.append(f"  ! {_one_line(note)}")

        return "\n".join(lines)

    def _render_reference(self, entry: ReferenceEntry) -> str:
        lines = [f"{entry.code} — {entry.title}"]
        lines.append("  Источник: справочник OBDex (CC0), перевод пока английский")
        if entry.description:
            lines.append(f"  Смысл: {_trim(entry.description, 600)}")
        if entry.causes:
            lines.append("  Вероятные причины:")
            for cause in entry.causes:
                likelihood = LIKELIHOOD_RU.get(cause.get("likelihood", ""), "")
                label = cause.get("en") or ""
                lines.append(f"    - {label}" + (f" ({likelihood})" if likelihood else ""))
        if entry.symptoms:
            lines.append("  Симптомы: " + "; ".join(_one_line(s) for s in entry.symptoms[:4]))
        details = []
        if entry.difficulty:
            details.append(f"сложность: {DIFFICULTY_RU.get(entry.difficulty, entry.difficulty)}")
        if entry.cost and entry.cost[1] > 0:
            details.append(f"ремонт: {entry.cost[0]:.0f}–{entry.cost[1]:.0f} EUR")
        if entry.flags.get("safety_critical"):
            details.append("система безопасности")
        if details:
            lines.append("  " + "; ".join(details))
        return "\n".join(lines)


def _one_line(text: str) -> str:
    return " ".join(str(text).split())


def _trim(text: str, limit: int) -> str:
    flat = _one_line(text)
    if len(flat) <= limit:
        return flat
    cut = flat[:limit].rsplit(" ", 1)[0]
    return cut + " …"
