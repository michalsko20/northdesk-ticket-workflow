# Northdesk — uruchomienie

Backend obsługi zgłoszeń: FastAPI, Temporal i agent z narzędziami tylko do odczytu.
Model proponuje decyzję, reguły w kodzie ją sprawdzają, a refund wymaga decyzji
człowieka. Helpdesk i refundy są lokalną symulacją, bez prawdziwych płatności.

## Start bez kluczy API

Potrzebujesz działającego Dockera z Compose. W katalogu repozytorium uruchom:

```bash
LLM_MODE=fake HELPDESK_FAILURE_RATE=0 docker compose up --build -d --wait
```

- Swagger: <http://localhost:8000/docs>
- Temporal UI: <http://localhost:8233>
- API/Temporal: <http://localhost:8000/health/ready>
- Ostatnio aktywni workerzy: <http://localhost:8000/health/worker>

Tryb `fake` nie wywołuje płatnego modelu. `HELPDESK_FAILURE_RATE=0` wyłącza losowe
błędy na czas prezentacji. Zwykła konfiguracja domyślnie symuluje błędy z
prawdopodobieństwem 0,2.

## Demo całego przepływu

Na hoście z Pythonem 3.9+:

```bash
python3 scripts/demo.py --suffix=-demo-$(date +%s) --approve-refunds
```

Skrypt przechodzi przez siedem przykładów, w tym zatwierdzenie symulowanego
refundu. Bez `--approve-refunds` zatrzyma ten przypadek na oczekiwaniu na człowieka.
Unikalny suffix uruchamia nowe zgłoszenia; ponowne użycie tego samego ID zwraca
oryginalny workflow i nie zmienia jego treści.

Możesz też użyć przycisków **Try it out → Execute** w Swaggerze:

1. `POST /tickets`: klient `C-100`, temat `Charged twice`, treść `Please refund`.
2. `GET /tickets/{ticket_id}`: zaczekaj na `step=awaiting_approval`.
3. `POST /tickets/{ticket_id}/approval`: `approved=true`, `reviewer=demo-operator`.
4. Odczytaj wynik i zobacz historię w Temporal UI.

`approved=false` lub brak odpowiedzi przez 24 godziny powoduje eskalację.
Zakończone zgłoszenie jest czytelne również po zatrzymaniu workera, bo API pobiera
utrwalony wynik. Aktywne zgłoszenia wymagają workera do Query.

## GLM i OpenAI

Opcjonalnie skopiuj `.env.example` do ignorowanego `.env` i ustaw własny klucz.
Możesz też przekazać zmienne przez środowisko — nie zapisuj kluczy w Git.

GLM używa `ZAI_API_KEY`, `GLM_MODEL` i `GLM_BASE_URL`. Z załadowanym profilem:

```bash
LLM_MODE=glm docker compose up --build -d --wait
python3 scripts/demo.py --suffix=-glm-$(date +%s) --approve-refunds
```

OpenAI używa `OPENAI_API_KEY` oraz `LLM_MODEL`:

```bash
LLM_MODE=openai docker compose up --build -d --wait
```

Tryby rzeczywiste mogą naliczać koszt wywołań modelu. Domyślny model GLM to
`glm-5.3-flash`, OpenAI to `gpt-4.1-mini`; dostępność zależy od konta.

## Testy

```bash
make test
```

Buduje kontener i uruchamia Ruff, formatowanie, mypy oraz testy bez sieci
zewnętrznej. Budowanie obrazu wymaga dostępu do zależności. Testy zwykłe nie
potrzebują kluczy ani osobno uruchomionego stosu aplikacji.

Trzy dodatkowe testy rzeczywistych timeoutów trwają łącznie około siedmiu minut:

```bash
docker compose --profile test run --rm --no-deps -e RUN_SLOW_TIMEOUT_TESTS=1 tests pytest tests/slow/test_activity_timeouts.py -q
```

Lokalne środowisko developerskie wymaga `uv` i Pythona 3.12:

```bash
uv sync --locked
uv run scripts/download_test_server.py
make check
uv run --locked pytest -q
```

## Limity i diagnostyka

- HTTP: maksymalnie 256 KiB danych, także chunked; 10 s na odbiór body.
- Domyślnie 32 obsługiwane żądania biznesowe i 4 aktywne Query na proces API.
  Query nieobsłużone w ciągu 1 s zwraca 503 z zaleceniem ponowienia.
- Worker: 10 aktywności, 16 zadań workflow, cache 512 workflow.
- Propozycja: 64 KiB; wiadomość/powód: 16 KiB; argumenty narzędzi: 16 KiB.
- `503` z `Retry-After`: odczekaj i ponów, zachowując to samo ID.
- `413`: za dużo bajtów; `422`: błędne pola; `408`: body przychodziło zbyt długo.
- `payload_warning`: widok pomija stare niepoprawne lub zbyt duże dane audytowe;
  decyzja i identyfikator zapisanego efektu pozostają zachowane.

Konfigurację oraz pełny kontrakt HTTP znajdziesz w [README](README.md#runtime-limits-and-health).
Health workera sprawdza pollerów z ostatnich 120 sekund, a nie obiecuje braku
opóźnień kolejki.

```bash
docker compose logs -f api worker
docker compose restart worker
docker compose down
```

`down` zachowuje wolumeny. Nie używaj `down -v`, jeśli chcesz zachować historię
i potwierdzenia efektów.

## Schemat i wyniki

[Schemat SVG](docs/architecture.svg) · [Schemat PNG](docs/architecture.png) ·
[Edytowalne źródło Mermaid](docs/architecture.mmd) ·
[Ocena architektury ze źródłami](docs/architecture.md) ·
[Naprawy i ich weryfikacja](docs/fixes.md).

Wdrożenie produkcyjne pozostaje osobnym zadaniem: potrzebuje m.in. uwierzytelnienia,
prawdziwego kontraktu idempotencji Helpdesku, wspólnej bazy i kontroli sumy refundów
na różnych zgłoszeniach. Aktualny zakres to działający, sprawdzony backend lokalny.
