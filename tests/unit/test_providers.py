"""The provider port: Gemini, the recorded replay, and the contract they share.

No test here reaches the network. The Gemini provider is exercised through an
injected stub client, which is what the ``client`` constructor argument exists
for; the recorded provider is offline by construction.

The behaviour these tests protect is the same in both directions. Any provider
failure — no key, a transport error, an empty response, text that is not JSON —
must surface as :class:`ProviderError` and nothing else, because the engine
catches exactly that and turns it into an empty fact set. An exception of another
type would escape the engine and take down the call-completion webhook; a
silently-empty dict would look like a completed interview.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.intelligence.engine import CallProofEngine
from app.intelligence.providers import (
    GeminiProvider,
    LLMProvider,
    ProviderError,
    RecordedProvider,
)
from app.intelligence.providers import gemini as gemini_module

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "transcripts"
EXTRACTION = {"consent": True, "facts": {"age_years": {"value": 30, "state": "STATED"}}}

#: Captured before the autouse fixture below stubs it out, so the tests that
#: exercise the real dotenv loader still have a handle on it.
REAL_LOAD_ENV_FILE = gemini_module.load_env_file


class StubClient:
    """Stands in for ``genai.Client``, recording the call it received."""

    def __init__(self, text: str | None = None, raises: Exception | None = None) -> None:
        self.text = text
        self.raises = raises
        self.calls: list[dict[str, Any]] = []
        self.models = self

    def generate_content(self, *, model: str, contents: str, config: Any) -> Any:
        self.calls.append({"model": model, "contents": contents, "config": config})
        if self.raises is not None:
            raise self.raises
        return type("Response", (), {"text": self.text})()


def _gemini(**kwargs: Any) -> GeminiProvider:
    return GeminiProvider(client=StubClient(**kwargs))


@pytest.fixture(autouse=True)
def _ignore_any_real_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the developer's own ``.env`` out of these tests.

    ``api_keys_from_environment`` falls back to reading ``.env``, so without this
    the suite would pass or fail depending on whether a real key file happens to
    sit in the working directory. The loader itself is covered below against
    files this test writes.
    """

    monkeypatch.setattr(gemini_module, "load_env_file", lambda *args, **kwargs: {})


# --------------------------------------------------------------------------- #
# The shared contract
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "provider",
    [RecordedProvider(), GeminiProvider(client=StubClient(text="{}"))],
    ids=["recorded", "gemini"],
)
def test_every_provider_satisfies_the_port(provider: object) -> None:
    assert isinstance(provider, LLMProvider)
    assert isinstance(provider.name, str) and provider.name


def test_provider_error_is_catchable_as_a_runtime_error() -> None:
    """The engine catches ProviderError; nothing else may escape a provider."""

    assert issubclass(ProviderError, RuntimeError)


# --------------------------------------------------------------------------- #
# Gemini
# --------------------------------------------------------------------------- #


def test_gemini_returns_the_parsed_object() -> None:
    provider = _gemini(text=json.dumps(EXTRACTION))
    assert provider.complete_json(system="rules", user="transcript") == EXTRACTION


def test_gemini_asks_for_json_at_temperature_zero() -> None:
    """Determinism is a requirement, not a preference.

    Two runs over one transcript must agree; a finding that moves between runs
    cannot be shown to an employer as verification.
    """

    client = StubClient(text="{}")
    GeminiProvider(client=client).complete_json(system="rules", user="transcript")

    config = client.calls[0]["config"]
    assert config.temperature == 0.0
    assert config.response_mime_type == "application/json"
    assert config.system_instruction == "rules"
    assert client.calls[0]["contents"] == "transcript"


def test_gemini_uses_the_default_model_and_accepts_an_override() -> None:
    assert _gemini(text="{}").model == gemini_module.DEFAULT_MODEL
    assert GeminiProvider(client=StubClient(text="{}"), model="gemini-3-pro").model == "gemini-3-pro"


def test_a_transport_failure_becomes_a_provider_error() -> None:
    provider = _gemini(raises=TimeoutError("connection reset"))
    with pytest.raises(ProviderError, match="Gemini request failed"):
        provider.complete_json(system="rules", user="transcript")


@pytest.mark.parametrize("text", [None, "", "   "], ids=["none", "empty", "blank"])
def test_an_empty_response_becomes_a_provider_error(text: str | None) -> None:
    with pytest.raises(ProviderError):
        _gemini(text=text).complete_json(system="rules", user="transcript")


def test_prose_instead_of_json_becomes_a_provider_error() -> None:
    """Models sometimes explain themselves. That is not an extraction."""

    provider = _gemini(text="I'm sorry, I can't help with that.")
    with pytest.raises(ProviderError, match="not JSON"):
        provider.complete_json(system="rules", user="transcript")


@pytest.mark.parametrize("text", ["[]", '"a string"', "42", "null"])
def test_valid_json_that_is_not_an_object_becomes_a_provider_error(text: str) -> None:
    with pytest.raises(ProviderError, match="expected an object"):
        _gemini(text=text).complete_json(system="rules", user="transcript")


def test_a_missing_api_key_fails_at_construction_not_mid_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Better to fail on startup than three minutes into a live interview."""

    for variable in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    with pytest.raises(ProviderError, match="No Gemini API key found"):
        GeminiProvider()


@pytest.mark.parametrize("variable", gemini_module.API_KEY_ENV_VARS)
def test_either_key_variable_is_accepted(
    variable: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    for name in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv(variable, "test-key")
    assert gemini_module.api_key_from_environment() == "test-key"


def test_an_explicit_client_needs_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """How every test in this repository avoids needing a real key."""

    for variable in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    assert GeminiProvider(client=StubClient(text="{}")).name == "gemini"


def test_importing_the_engine_never_requires_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """``build_default_engine`` constructs Gemini lazily, so import stays safe."""

    for variable in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(variable, raising=False)
    from app.intelligence import engine as engine_module

    assert engine_module.CallProofEngine is not None
    with pytest.raises(ProviderError):
        engine_module.build_default_engine()


# --------------------------------------------------------------------------- #
# Recorded replay
# --------------------------------------------------------------------------- #


def test_a_recording_is_served_for_its_own_transcript() -> None:
    provider = RecordedProvider({"Worker: I am thirty.": EXTRACTION})
    assert provider.complete_json(system="rules", user="Transcript:\n\nWorker: I am thirty.") == EXTRACTION


def test_a_recording_is_never_served_for_another_transcript() -> None:
    """Keying on the transcript is what makes the fixtures trustworthy.

    Keyed on the worker or the call instead, a fixture could answer for a
    transcript it was never produced from, and the test would still pass.
    """

    provider = RecordedProvider({"Worker: I am thirty.": EXTRACTION})
    with pytest.raises(ProviderError, match="No recorded extraction matches"):
        provider.complete_json(system="rules", user="Transcript:\n\nWorker: I would rather not say.")


def test_whitespace_differences_still_match() -> None:
    """TeleExpert may re-wrap a transcript; that is not a different interview."""

    provider = RecordedProvider({"Worker: I am\nthirty.": EXTRACTION})
    assert provider.complete_json(system="rules", user="Worker: I am thirty.") == EXTRACTION


def test_a_recording_cannot_be_mutated_by_its_caller() -> None:
    """Two tests replaying one fixture must not contaminate each other."""

    provider = RecordedProvider({"Worker: I am thirty.": EXTRACTION})
    first = provider.complete_json(system="rules", user="Worker: I am thirty.")
    first["consent"] = False
    second = provider.complete_json(system="rules", user="Worker: I am thirty.")
    assert second["consent"] is True


def test_an_empty_provider_matches_nothing() -> None:
    with pytest.raises(ProviderError):
        RecordedProvider().complete_json(system="rules", user="anything")


def test_the_fixture_directory_loads_every_transcript() -> None:
    provider = RecordedProvider.from_fixture_dir(FIXTURE_DIR)
    for path in sorted(FIXTURE_DIR.glob("*.json")):
        fixture = json.loads(path.read_text(encoding="utf-8"))
        replayed = provider.complete_json(
            system="rules", user=f"Transcript:\n\n{fixture['transcript']}"
        )
        assert replayed == fixture["expected_extraction"]


def test_an_empty_directory_is_an_error_not_a_silent_no_op() -> None:
    """A typo'd path must not produce a provider that answers nothing."""

    with pytest.raises(ProviderError, match="No transcript fixtures found"):
        RecordedProvider.from_fixture_dir(Path(__file__).parent)


def test_the_system_prompt_does_not_affect_replay() -> None:
    """A recording is a recording; only the transcript identifies it."""

    provider = RecordedProvider({"Worker: I am thirty.": EXTRACTION})
    assert provider.complete_json(system="", user="Worker: I am thirty.") == EXTRACTION
    assert provider.complete_json(system="anything", user="Worker: I am thirty.") == EXTRACTION


# --------------------------------------------------------------------------- #
# The engine's handling of a failed provider
# --------------------------------------------------------------------------- #


class BrokenProvider:
    name = "broken"

    def complete_json(self, *, system: str, user: str) -> dict[str, Any]:
        raise ProviderError("nothing works")


def test_a_failed_extraction_is_recorded_and_confirms_nothing() -> None:
    """The fail-safe direction: an outage looks like no evidence, not a good job."""

    engine = CallProofEngine(BrokenProvider())
    extracted = engine.extract_evidence("Worker: I am thirty.", "W001")

    assert extracted["extraction_error"] is True
    assert extracted["consent"] is False
    assert extracted["worker_id"] == "W001"
    assert extracted["transcript"] == "Worker: I am thirty."
    assert engine.evaluate_clauses(extracted).overall_verdict == "UNCLEAR"


def test_the_engine_records_which_provider_answered() -> None:
    """An audit trail needs to say what produced a finding."""

    engine = CallProofEngine(RecordedProvider({"Worker: I am thirty.": EXTRACTION}))
    assert engine.extract_evidence("Worker: I am thirty.", "W001")["provider"] == "recorded"
    assert CallProofEngine(BrokenProvider()).extract_evidence("x", "W001")["provider"] == "broken"


def test_the_engine_fills_every_expected_fact_whatever_the_model_omits() -> None:
    """A short response must not leave a clause key missing downstream."""

    engine = CallProofEngine(RecordedProvider({"Worker: I am thirty.": EXTRACTION}))
    extracted = engine.extract_evidence("Worker: I am thirty.", "W001")

    from app.intelligence.engine import EXPECTED_FACTS

    assert set(extracted["facts"]) == set(EXPECTED_FACTS)
    assert engine.evaluate_clauses(extracted).overall_verdict == "UNCLEAR"


def test_the_model_cannot_reassign_the_worker() -> None:
    """Identity comes from the call record, never from the transcript reader."""

    engine = CallProofEngine(
        RecordedProvider({"Worker: I am thirty.": dict(EXTRACTION, worker_id="W999")})
    )
    assert engine.extract_evidence("Worker: I am thirty.", "W001")["worker_id"] == "W001"


# --------------------------------------------------------------------------- #
# Reading many keys from one environment variable
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["a","b","c"]', ["a", "b", "c"]),
        ('  ["a", "b"]  ', ["a", "b"]),
        ("a,b,c", ["a", "b", "c"]),
        ("a, b , c", ["a", "b", "c"]),
        ("a b c", ["a", "b", "c"]),
        ("single", ["single"]),
        ('["a","a","b"]', ["a", "b"]),
        ("", []),
        ("   ", []),
        (None, []),
    ],
    ids=[
        "json", "json-padded", "commas", "commas-spaced", "whitespace",
        "single", "deduped", "empty", "blank", "none",
    ],
)
def test_one_variable_can_carry_one_key_or_many(raw: str | None, expected: list[str]) -> None:
    """The project ships a JSON array; the other forms cost nothing to accept."""

    assert gemini_module.parse_api_keys(raw) == expected


def test_a_duplicated_key_does_not_get_double_the_traffic() -> None:
    """Deduplication is about quota, not tidiness: two slots, one limit."""

    assert gemini_module.parse_api_keys('["k1","k2","k1"]') == ["k1", "k2"]


def test_malformed_json_is_an_error_not_a_silent_single_key() -> None:
    """Otherwise a typo'd array becomes one absurd key and every call 401s."""

    with pytest.raises(ProviderError, match="does not parse"):
        gemini_module.parse_api_keys('["a","b"')


def test_json_that_is_not_an_array_is_an_error() -> None:
    with pytest.raises(ProviderError, match="must be an array"):
        gemini_module.parse_api_keys('{"key": "a"}')


def test_the_environment_yields_every_key(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("GEMINI_API_KEY", '["k1","k2","k3"]')

    assert gemini_module.api_keys_from_environment() == ["k1", "k2", "k3"]
    assert gemini_module.api_key_from_environment() == "k1"
    assert GeminiProvider().key_count == 3


def test_the_first_variable_with_keys_wins(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", '["primary"]')
    monkeypatch.setenv("GOOGLE_API_KEY", '["fallback"]')
    assert gemini_module.api_keys_from_environment() == ["primary"]


def test_an_empty_first_variable_falls_through(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GEMINI_API_KEY", "")
    monkeypatch.setenv("GOOGLE_API_KEY", '["fallback"]')
    assert gemini_module.api_keys_from_environment() == ["fallback"]


#: Shaped like the real thing, deliberately not real. Google's two key formats
#: are ``AIzaSy`` + 33 chars and the ``AQ.Ab8`` OAuth-style form; the masking and
#: rotation tests need both shapes, and a committed file must never hold a live
#: credential.
FAKE_KEYS = (
    "AIzaSyFAKE0000000000000000000000000000A",
    "AIzaSyFAKE1111111111111111111111111111B",
    "AQ.Ab8FAKE22222222222222222222222222222C",
)


@pytest.mark.parametrize("key", [FAKE_KEYS[0], FAKE_KEYS[2]], ids=["aiza", "oauth"])
def test_a_masked_key_reveals_neither_end_in_full(key: str) -> None:
    masked = gemini_module.mask_key(key)
    assert key not in masked
    assert len(masked) < len(key)
    assert masked.endswith(key[-4:])


def test_a_short_secret_is_masked_entirely() -> None:
    """No partial reveal of something too short to mask safely."""

    assert gemini_module.mask_key("tiny") == "***"


# --------------------------------------------------------------------------- #
# Rotation and failover
# --------------------------------------------------------------------------- #


QUOTA = "429 RESOURCE_EXHAUSTED: quota exceeded"
GATED_MODEL = "404 NOT_FOUND: this model is no longer available to new users"
DEAD = "403 PERMISSION_DENIED: API_KEY_INVALID"


def _rotating(*stubs: StubClient) -> GeminiProvider:
    return GeminiProvider(clients=list(stubs))


def test_successive_calls_start_on_different_keys() -> None:
    """Round-robin is what spreads a per-minute limit across the pool."""

    first, second = StubClient(text="{}"), StubClient(text="{}")
    provider = _rotating(first, second)

    for _ in range(4):
        provider.complete_json(system="rules", user="transcript")

    assert len(first.calls) == 2
    assert len(second.calls) == 2


def test_an_exhausted_key_fails_over_to_the_next() -> None:
    exhausted = StubClient(raises=RuntimeError(QUOTA))
    healthy = StubClient(text=json.dumps(EXTRACTION))

    assert _rotating(exhausted, healthy).complete_json(system="r", user="t") == EXTRACTION
    assert len(exhausted.calls) == 1 and len(healthy.calls) == 1


def test_a_model_gated_to_older_projects_fails_over() -> None:
    """The failure this pool actually hits.

    Google gates some models per project, so one key is refused a model the next
    key serves. A 404 therefore has to rotate rather than abort the interview.
    """

    gated = StubClient(raises=RuntimeError(GATED_MODEL))
    allowed = StubClient(text=json.dumps(EXTRACTION))

    assert _rotating(gated, allowed).complete_json(system="r", user="t") == EXTRACTION


def test_every_key_exhausted_is_a_provider_error() -> None:
    provider = _rotating(*[StubClient(raises=RuntimeError(QUOTA)) for _ in range(3)])
    with pytest.raises(ProviderError, match="All 3 Gemini key"):
        provider.complete_json(system="r", user="t")


def test_a_rejected_key_is_parked_and_not_retried() -> None:
    """A key that will never work must not cost a retry on every later call."""

    rejected = StubClient(raises=RuntimeError(DEAD))
    healthy = StubClient(text=json.dumps(EXTRACTION))
    provider = _rotating(rejected, healthy)

    for _ in range(3):
        assert provider.complete_json(system="r", user="t") == EXTRACTION

    assert len(rejected.calls) == 1, "parked key was retried"
    assert provider.key_count == 1


def test_all_keys_rejected_says_so_plainly() -> None:
    provider = _rotating(*[StubClient(raises=RuntimeError(DEAD)) for _ in range(2)])
    with pytest.raises(ProviderError):
        provider.complete_json(system="r", user="t")
    with pytest.raises(ProviderError, match="none left to try"):
        provider.complete_json(system="r", user="t")


def test_a_bad_request_is_not_retried_across_the_pool() -> None:
    """Twelve identical 400s waste twelve calls and still fail."""

    clients = [StubClient(raises=RuntimeError("400 INVALID_ARGUMENT: bad request")) for _ in range(3)]
    with pytest.raises(ProviderError, match="400"):
        _rotating(*clients).complete_json(system="r", user="t")

    assert sum(len(client.calls) for client in clients) == 1


def test_prose_from_one_key_is_not_retried_on_the_others() -> None:
    """A model that chatted instead of answering will chat on every key."""

    clients = [StubClient(text="I'm sorry, I can't help.") for _ in range(3)]
    with pytest.raises(ProviderError, match="not JSON"):
        _rotating(*clients).complete_json(system="r", user="t")

    assert sum(len(client.calls) for client in clients) == 1


def test_a_single_key_still_works_unrotated() -> None:
    assert _rotating(StubClient(text=json.dumps(EXTRACTION))).complete_json(
        system="r", user="t"
    ) == EXTRACTION


def test_a_failure_message_never_carries_a_whole_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Error text reaches logs. A full key must never travel with it."""

    secret = FAKE_KEYS[0]
    provider = GeminiProvider(api_keys=[secret, FAKE_KEYS[1]])
    monkeypatch.setattr(
        GeminiProvider, "_client_for", lambda self, key: StubClient(raises=RuntimeError(QUOTA))
    )

    with pytest.raises(ProviderError) as caught:
        provider.complete_json(system="r", user="t")
    assert secret not in str(caught.value)
    assert secret[:10] in str(caught.value), "masked prefix aids debugging"


def test_concurrent_calls_do_not_lose_the_cursor() -> None:
    """Member B's sync endpoints run in a threadpool, so this races in practice."""

    import threading

    clients = [StubClient(text="{}") for _ in range(4)]
    provider = _rotating(*clients)
    threads = [
        threading.Thread(target=lambda: provider.complete_json(system="r", user="t"))
        for _ in range(40)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert sum(len(client.calls) for client in clients) == 40
    assert all(client.calls for client in clients), "a key was never used"


def test_the_default_model_is_not_the_gated_one() -> None:
    """Pinning this: gemini-2.5-flash 404s for keys created after the cutoff."""

    assert gemini_module.DEFAULT_MODEL != "gemini-2.5-flash"


def test_rotation_does_not_change_what_the_model_is_asked() -> None:
    """Determinism survives rotation: only the credential differs."""

    first, second = StubClient(text="{}"), StubClient(text="{}")
    provider = _rotating(first, second)
    provider.complete_json(system="rules", user="transcript")
    provider.complete_json(system="rules", user="transcript")

    sent = [client.calls[0] for client in (first, second)]
    assert sent[0]["contents"] == sent[1]["contents"] == "transcript"
    assert sent[0]["config"].temperature == sent[1]["config"].temperature == 0.0


# --------------------------------------------------------------------------- #
# The .env fallback
# --------------------------------------------------------------------------- #


def test_a_dotenv_file_supplies_keys_when_the_environment_is_bare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Nothing else in the application loads .env, so this is the only path."""

    path = tmp_path / ".env"
    path.write_text('GEMINI_API_KEY=["k1","k2"]\n', encoding="utf-8")
    monkeypatch.setattr(gemini_module, "load_env_file", lambda *a, **k: REAL_LOAD_ENV_FILE(path))

    for name in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    assert gemini_module.api_keys_from_environment() == ["k1", "k2"]


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ('GEMINI_API_KEY=["a","b"]', '["a","b"]'),
        ("GEMINI_API_KEY='[\"a\"]'", '["a"]'),
        ("GEMINI_API_KEY=bare", "bare"),
        ("  GEMINI_API_KEY = spaced  ", "spaced"),
    ],
    ids=["json", "single-quoted", "bare", "spaced"],
)
def test_the_loader_strips_one_layer_of_quotes(
    tmp_path: Path, line: str, expected: str
) -> None:
    """A pasted JSON array is normally quoted; the quotes are not the value."""

    path = tmp_path / ".env"
    path.write_text(line + "\n", encoding="utf-8")
    assert REAL_LOAD_ENV_FILE(path)["GEMINI_API_KEY"] == expected


def test_the_loader_skips_comments_and_blanks(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("# a comment\n\nGEMINI_API_KEY=k\nnot-a-pair\n", encoding="utf-8")

    assert REAL_LOAD_ENV_FILE(path) == {"GEMINI_API_KEY": "k"}


def test_a_missing_dotenv_is_not_an_error(tmp_path: Path) -> None:
    """Most deployments export real variables and ship no file."""

    assert REAL_LOAD_ENV_FILE(tmp_path / "absent") == {}


def test_a_real_export_beats_the_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A file must never override what an operator set deliberately."""

    path = tmp_path / ".env"
    path.write_text('GEMINI_API_KEY=["from-file"]\n', encoding="utf-8")
    monkeypatch.setattr(gemini_module, "load_env_file", lambda *a, **k: REAL_LOAD_ENV_FILE(path))
    monkeypatch.setenv("GEMINI_API_KEY", '["from-environment"]')

    assert gemini_module.api_keys_from_environment() == ["from-environment"]


def test_the_project_dotenv_parses_if_it_exists() -> None:
    """Guards the real file: a malformed .env must fail loudly here, not on stage."""

    loaded = REAL_LOAD_ENV_FILE(Path(__file__).resolve().parents[2] / ".env")
    if "GEMINI_API_KEY" not in loaded:
        pytest.skip("no local .env")

    keys = gemini_module.parse_api_keys(loaded["GEMINI_API_KEY"])
    assert keys, "GEMINI_API_KEY present but yielded no keys"
    assert all(not key.startswith(("'", '"')) for key in keys), "quotes leaked into a key"


# --------------------------------------------------------------------------- #
# Registration: the gap that made the live app answer 503
# --------------------------------------------------------------------------- #


@pytest.fixture
def _clean_registry() -> Any:
    """Restore whatever was registered, so these tests do not leak state."""

    from app.intelligence import registry

    previous = registry._engine
    yield registry
    registry._engine = previous


def test_an_unregistered_engine_is_an_error_not_a_silent_default(_clean_registry: Any) -> None:
    """Why the endpoint answers 503 rather than inventing an extraction."""

    _clean_registry._engine = None
    with pytest.raises(RuntimeError, match="has not been configured"):
        _clean_registry.get_engine()


def test_registering_makes_the_engine_reachable_by_member_b(_clean_registry: Any) -> None:
    """The wiring no test covered, which is how the 503 went unnoticed."""

    from app.intelligence.engine import register_default_engine

    _clean_registry._engine = None
    engine = register_default_engine(RecordedProvider({"t": EXTRACTION}))

    assert _clean_registry.get_engine() is engine


def test_startup_registration_survives_a_missing_key(
    _clean_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``try_register_default_engine`` is safe to call unconditionally at startup.

    ``register_default_engine`` is not: with no key it raises, which would stop
    the application from booting at all.
    """

    from app.intelligence.engine import try_register_default_engine

    for name in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    _clean_registry._engine = None

    assert try_register_default_engine() is None
    with pytest.raises(RuntimeError):
        _clean_registry.get_engine()


def test_a_missing_key_is_never_papered_over_with_fixtures(
    _clean_registry: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Registering fixture data on a missing key would report invented interviews."""

    from app.intelligence.engine import try_register_default_engine

    for name in gemini_module.API_KEY_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    _clean_registry._engine = None
    try_register_default_engine()

    assert _clean_registry._engine is None


def test_startup_registration_returns_the_engine_when_a_provider_exists(
    _clean_registry: Any,
) -> None:
    from app.intelligence.engine import try_register_default_engine

    _clean_registry._engine = None
    engine = try_register_default_engine(RecordedProvider({"t": EXTRACTION}))

    assert engine is not None
    assert _clean_registry.get_engine() is engine
