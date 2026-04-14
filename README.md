# API de Cancelamento de Contrato

API REST em FastAPI para gerenciamento de contratos com cancelamento idempotente, proteção de concorrência e reprocessamento de contratos travados.

## Stack

- Python
- FastAPI
- SQLAlchemy
- PostgreSQL
- Alembic
- Docker / Docker Compose
- Pytest

## Como rodar

### 1. Variáveis de ambiente

A API requer a variável `DATABASE_URL` para conectar ao banco. Ao subir via Docker Compose ela é definida automaticamente no serviço `api`:

```yaml
# docker-compose.yml
environment:
  DATABASE_URL: postgresql+psycopg://postgres:postgres@db:5432/postgres
```

Para rodar a API **fora do Docker** (ex.: desenvolvimento local com `uvicorn`), copie o arquivo de exemplo e edite conforme necessário:

```bash
cp .env.example .env
```

O repositório inclui `.env.example` como referência das variáveis esperadas. O arquivo `.env` é ignorado pelo Git e nunca deve ser versionado.

### 2. Subir tudo com Docker

```bash
docker-compose up --build
```

Serviços:

- API: [http://localhost:8000](http://localhost:8000)
- Healthcheck: [http://localhost:8000/health](http://localhost:8000/health)
- Postgres: localhost:5432
- Adminer (SQL no navegador): [http://localhost:8080](http://localhost:8080)

Ao subir via Docker, a API aplica migrations automaticamente com Alembic (`alembic upgrade head`).

### 1.1 Acessar Postgres pelo navegador (Adminer)

1. Abra [http://localhost:8080](http://localhost:8080)
2. Preencha os campos de login:

- **System**: `PostgreSQL`
- **Server**: `db`
- **Username**: `postgres`
- **Password**: `postgres`
- **Database**: `postgres`

> Observação: se você acessar o Adminer fora do Docker (via browser local), o host do banco dentro da rede do compose continua sendo `db`.

### 3. Rodar testes

```bash
pip install -r app/requirements-tests.txt
pytest -q
```

Para incluir o teste de concorrência com lock real do PostgreSQL, defina `TEST_DATABASE_URL` antes de rodar o pytest:

```bash
TEST_DATABASE_URL=postgresql+psycopg://postgres:postgres@localhost:5432/postgres pytest -q
```

### 4. Ambiente de desenvolvimento (lint/format/pre-commit)

```bash
pip install -r app/requirements-dev.txt
pre-commit install
pre-commit run --all-files
```

## Endpoints

- `POST /contracts`
- `POST /contracts/{contract_id}/cancel` (header obrigatório `Idempotency-Key`)
- `POST /contracts/{contract_id}/reprocess`
- `GET /health`

## Migrações (Alembic)

Arquivos principais:

- `./alembic.ini` (na raiz do projeto)
- `alembic/env.py`
- `alembic/script.py.mako` (template usado para gerar novas revisions)
- `alembic/versions/0001_initial_contract_schema.py`

Comandos úteis:

```bash
alembic upgrade head
alembic downgrade -1
alembic revision -m "descricao" --autogenerate
```

## Regras de negócio implementadas

- Cancelamento permitido até 7 dias após `created_at`.
- Cancelamento exige `refundable_amount > 0`.
- Cancelamento idempotente por `Idempotency-Key` com persistência em `cancel_requests`.
- Proteção contra corrida de concorrência com `SELECT ... FOR UPDATE` e unique key.
- Reprocessamento permitido apenas quando `status = PROCESSING` e travado há mais de 5 minutos.
- Tratamento global de exceções com payload consistente (`detail`) e fallback 500.

## Decisões técnicas

- Arquitetura em camadas (`api`, `services`, `repositories`).
- Status modelados com `Enum`.
- Índices básicos para melhorar buscas frequentes (`contracts.status`, `cancel_requests.contract_id`).
- Logging com correlation id no formatter.
- Middleware HTTP para propagar `X-Correlation-ID` e manter o valor no contexto de log durante o request.
- Migrações versionadas com Alembic para controle explícito de schema.

## Pontos de melhoria

- Adicionar testes de concorrência com múltiplos workers/instâncias reais.
- Expandir observabilidade (métricas de latência e taxa de erro).

## Roadmap de melhorias futuras

### Curto prazo (baixo risco, alto retorno)

- Adicionar validações de schema com limites explícitos (ex.: `amount > 0`, `refundable_amount >= 0`).
- Adicionar paginação e filtros para consultas administrativas de contratos e cancelamentos.
- Introduzir `settings` centralizado (Pydantic Settings) para remover valores hardcoded.
- Melhorar mensagens de erro de domínio com códigos de erro estáveis (ex.: `CANCELLATION_WINDOW_EXPIRED`).

### Médio prazo (robustez operacional)

- Incluir métricas Prometheus (latência, taxa de erro, taxa de cancelamento, reprocessamentos).
- Adicionar tracing distribuído (OpenTelemetry) e propagação de correlation id entre serviços.
- Criar testes de carga e concorrência com múltiplos processos para validar lock e idempotência em produção.
- Implementar políticas de retry com backoff exponencial para integrações externas.

### Longo prazo (escala e evolução arquitetural)

- Evoluir o fluxo de cancelamento para processamento assíncrono com fila e worker dedicado.
- Implementar padrão outbox/inbox para publicação de eventos com idempotência e rastreabilidade.
- Separar leitura e escrita (CQRS leve) para relatórios sem impactar o caminho transacional.
- Adicionar estratégia de versionamento de API para evolução sem quebra de clientes.

### Melhorias de segurança e governança

- Adicionar autenticação e autorização (JWT/OAuth2) para endpoints sensíveis.
- Introduzir rate limiting por chave/API client.
- Implantar auditoria de alterações críticas (quem alterou, quando, antes/depois).
- Integrar SAST/DAST no pipeline e política de atualização de dependências.

## Consistência eventual

O cancelamento não é um evento propagado de forma assíncrona — ele é processado de forma síncrona e transacional dentro do request HTTP.
A tabela `cancel_requests` serve como registro de idempotência persistente: se o processo cair após o `flush` mas antes do `commit`, o status do `CancelRequest` permanece `PROCESSING` e o contrato não é alterado.
Nesse cenário, uma nova requisição com a mesma `Idempotency-Key` encontrará o registro `PROCESSING` e retornará seu estado atual, garantindo que o cliente receba uma resposta consistente sem processar o cancelamento duas vezes.
Se o sistema precisasse de processamento assíncrono real (ex.: filas), a consistência eventual seria garantida pelo mesmo mecanismo de idempotência — a key impediria duplicatas independente do número de retries.

## Testes importantes

Além dos cenários funcionais, existe um teste de corrida simulada:

- `tests/test_contracts.py::test_concurrent_cancel_race_same_key`
- `tests/test_contracts.py::test_concurrent_cancel_race_same_key_postgres_real_locking` (quando `TEST_DATABASE_URL` estiver definido)

Esse teste com PostgreSQL faz cleanup apenas dos dados que ele cria e não remove o schema do banco.

Ele dispara dois cancelamentos concorrentes com a mesma idempotency key e valida que apenas um registro de cancelamento persiste.

## Arquivos de dependências

- `app/requirements.txt`: runtime/produção
- `app/requirements-tests.txt`: testes (inclui runtime)
- `app/requirements-dev.txt`: desenvolvimento (inclui testes + lint/format + pre-commit)

## Padronização de código

O projeto usa configurações centralizadas em `pyproject.toml` para:

- `black` (formatação)
- `ruff` (lint + import sorting)
- `pytest` (opções padrão de execução)
