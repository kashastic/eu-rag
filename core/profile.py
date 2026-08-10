"""The asker's business context: four optional, closed-vocabulary fields.

Most EU obligations are threshold functions — of headcount, of member state, of
whether you build an AI system or merely use one — so the same question has
different correct answers for different askers. This module holds that context
and turns it into the sentence the answerer puts in front of the question.

**Why a closed vocabulary and not free text.** The context sentence is injected
OUTSIDE the `BEGIN SOURCES` fence, i.e. in the region of the prompt the model is
told to obey rather than to quote. Free text there would be a prompt-injection
surface — and a *stored* profile is worse than a per-query field, because it is
set once and then rides every future answer, including inside saved chats. So
the user picks from a list and **this module writes the sentence**; no
user-authored string ever reaches the trusted region. Callers must validate
against the mappings below (`api.routes.query.ProfileBody` does) — an unknown
value is dropped here rather than interpolated.

The vocabulary is deliberately small. Each field earns its place by flipping a
threshold that the corpus actually contains; anything that only makes the form
longer belongs nowhere near it.
"""

from dataclasses import dataclass

# --- vocabularies ---------------------------------------------------------
# value -> the English fragment used in the prompt sentence. These are also the
# only accepted wire values; the frontend carries its own display labels.

# 27 member states. Drives national implementation caveats and picks out the
# relevant one of the 10 national funding agencies in the corpus.
COUNTRIES: dict[str, str] = {
    "AT": "Austria",
    "BE": "Belgium",
    "BG": "Bulgaria",
    "HR": "Croatia",
    "CY": "Cyprus",
    "CZ": "Czechia",
    "DK": "Denmark",
    "EE": "Estonia",
    "FI": "Finland",
    "FR": "France",
    "DE": "Germany",
    "GR": "Greece",
    "HU": "Hungary",
    "IE": "Ireland",
    "IT": "Italy",
    "LV": "Latvia",
    "LT": "Lithuania",
    "LU": "Luxembourg",
    "MT": "Malta",
    "NL": "the Netherlands",
    "PL": "Poland",
    "PT": "Portugal",
    "RO": "Romania",
    "SK": "Slovakia",
    "SI": "Slovenia",
    "ES": "Spain",
    "SE": "Sweden",
    "non_eu": "a country outside the EU",
}

# Headcount bands only. The EU SME definition (Rec. 2003/361, in the corpus)
# also turns on turnover and balance-sheet totals, which is exactly why
# `describe` warns the model not to settle a size category from headcount alone.
SIZES: dict[str, str] = {
    "micro": "a micro business (fewer than 10 people)",
    "small": "a small business (10-49 people)",
    "medium": "a medium-sized business (50-249 people)",
    "large": "a large business (250 or more people)",
}

SECTORS: dict[str, str] = {
    "software": "software and IT services",
    "manufacturing": "manufacturing",
    "retail": "retail and e-commerce",
    "food": "food and drink",
    "healthcare": "healthcare and life sciences",
    "finance": "financial services",
    "professional": "professional services",
    "construction": "construction",
    "transport": "transport and logistics",
    "energy": "energy and utilities",
    "education": "education and training",
    "media": "creative industries and media",
    "agriculture": "agriculture",
    "hospitality": "hospitality and tourism",
    "other": "a sector outside the usual categories",
}

# The load-bearing AI Act distinction. A yes/no flag cannot separate these two,
# and their obligations differ by an order of magnitude, so the field asks for
# the role rather than for usage. Phrased in the Act's own vocabulary so the
# model can connect it to the Art. 3 definitions when they are retrieved --
# without this module asserting what either role owes.
AI_ROLES: dict[str, str] = {
    "none": "reports that it does not currently use AI systems",
    "deployer": (
        'uses AI systems supplied by others (a "deployer" in AI Act terms, '
        'not a "provider")'
    ),
    "provider": (
        "develops, rebrands or places AI systems on the market "
        '(a "provider" in AI Act terms rather than a "deployer")'
    ),
}


@dataclass(frozen=True)
class BusinessProfile:
    """All four fields are optional and independent. A profile with three
    `None`s is normal — the intro screen never requires an answer — so every
    consumer must handle the empty case."""

    country: str | None = None
    size: str | None = None
    sector: str | None = None
    ai_role: str | None = None

    @property
    def is_empty(self) -> bool:
        return not any((self.country, self.size, self.sector, self.ai_role))

    @classmethod
    def from_dict(cls, data: dict | None) -> "BusinessProfile":
        if not data:
            return cls()
        return cls(
            country=data.get("country"),
            size=data.get("size"),
            sector=data.get("sector"),
            ai_role=data.get("ai_role"),
        )

    def to_dict(self) -> dict[str, str | None]:
        return {
            "country": self.country,
            "size": self.size,
            "sector": self.sector,
            "ai_role": self.ai_role,
        }

    def log_summary(self) -> str:
        """Compact, ASCII, no spaces — this goes on the `query outcome:` line,
        which is the denominator for every per-query rate and is read with
        grep. A non-ASCII character in a grep-targeted log string has broken a
        count here before."""
        parts = [
            f"{name}={value}"
            for name, value in (
                ("country", self.country),
                ("size", self.size),
                ("sector", self.sector),
                ("ai", self.ai_role),
            )
            if value
        ]
        return ",".join(parts) if parts else "none"

    def describe(self) -> str:
        """The prompt fragment, or `""` when nothing is set.

        Unknown values are skipped rather than interpolated: validation happens
        at the API boundary, and this is the second line of defence keeping
        arbitrary strings out of the trusted region of the prompt.
        """
        clauses: list[str] = []
        if self.size in SIZES:
            clauses.append(SIZES[self.size])
        if self.sector in SECTORS:
            clauses.append(f"operating in {SECTORS[self.sector]}")
        if self.country in COUNTRIES:
            clauses.append(f"based in {COUNTRIES[self.country]}")
        if self.ai_role in AI_ROLES:
            clauses.append(AI_ROLES[self.ai_role])
        if not clauses:
            return ""

        described = "; ".join(clauses)
        return (
            "Context about the asker, supplied by them and NOT drawn from the "
            f"sources: {described}.\n\n"
            "Use this only to judge which parts of the sources are relevant and "
            "how to frame the answer. It is not evidence. Never state that an "
            "obligation, threshold or exemption applies to them unless a cited "
            "source supports it, and say plainly when the sources do not cover "
            "their situation. Where a size category in EU law also turns on "
            "turnover or balance-sheet totals, headcount alone does not settle "
            "it. If the sources do not answer the question for this asker, say "
            "so — their profile is never a substitute for a source.\n\n"
        )
