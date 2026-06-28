# Card Customer Assist Agent

A credit card support chatbot — **"Penny" for Northwind Bank** — that helps signed-in
customers with exactly two things: **making a payment** and **viewing transactions**.

Like the [content-builder-agent](../content-builder-agent), the agent is defined through
filesystem primitives, not hardcoded logic:

- **Memory** (`AGENTS.md`) — persona, scope, and security rules, always in the prompt
- **Skills** (`skills/*/SKILL.md`) — the payment and transaction workflows, loaded on demand
- **Subagents** (`subagents.yaml`) — a `spending-analyst` for "how much did I spend on…" questions
- **Mock tools** (`card_assist.py`) — a fake in-memory bank backend so you can try it end to end

It runs as an **interactive chat**, so multi-step flows (like confirming a payment) work naturally.

## Quick Start

```bash
export ANTHROPIC_API_KEY="..."

cd examples/card-customer-assist
uv run python card_assist.py
```

You'll get a prompt. Try things like:

```
show my recent transactions
what is charge txn_1031
how much did I spend on dining
pay the minimum
pay $200 from my checking account
```

Or run a single message without entering the chat:

```bash
uv run python card_assist.py "What's my balance and minimum due?"
```

## How It Works

```
card-customer-assist/
├── AGENTS.md                    # Persona, scope, security rules (always loaded)
├── subagents.yaml               # spending-analyst subagent
├── skills/
│   ├── make-payment/SKILL.md    # Payment workflow + confirmation guardrails
│   └── view-transactions/SKILL.md
└── card_assist.py               # Mock bank tools + interactive chat loop
```

| File | Purpose | When loaded |
|------|---------|-------------|
| `AGENTS.md` | Who Penny is, what's in scope, security rules | Always (system prompt) |
| `skills/make-payment/SKILL.md` | Show balance → pick amount → pick method → **confirm** → pay | On demand |
| `skills/view-transactions/SKILL.md` | List activity, explain a charge, or delegate a spending summary | On demand |
| `subagents.yaml` | `spending-analyst` for aggregation questions | Always (defines the `task` tool) |

**Flow for a payment:**
1. Customer says "pay my bill" → agent loads the `make-payment` skill
2. Calls `get_card_summary()` and shows balance + minimum due
3. Helps choose an amount and a payment method (`list_payment_methods()`)
4. **Confirms the exact amount and method**, then calls `make_payment(...)`
5. Reports the confirmation number and new balance

**Flow for a spending question:**
1. "How much did I spend on dining?" → agent loads `view-transactions`
2. Delegates to the `spending-analyst` subagent via the `task` tool
3. The subagent pulls transactions, totals them, and returns a summary the agent relays

## The Mock Tools

All in `card_assist.py`, backed by in-memory dicts for one signed-in customer:

| Tool | What it does |
|------|--------------|
| `get_card_summary()` | Balance, minimum due, due date, available credit |
| `list_transactions(limit, category, search)` | Recent activity, with optional filters |
| `get_transaction(transaction_id)` | Full detail for one charge |
| `list_payment_methods()` | Bank accounts on file (last 4 only) |
| `make_payment(amount, payment_method_id)` | Processes a payment, returns a confirmation |

Payments actually mutate the in-memory balance for the session, so you can pay, then
re-check your balance and see it go down.

## Customizing

- **Change the persona / rules:** edit `AGENTS.md` (e.g., tone, what's out of scope).
- **Change a workflow:** edit `skills/make-payment/SKILL.md` or `view-transactions/SKILL.md`.
- **Add a content/data type:** create `skills/<name>/SKILL.md` with `name` + `description` frontmatter.
- **Tune the analyst:** edit `subagents.yaml` (its model is `claude-haiku-4-5` to keep it cheap).
- **Swap mock tools for real APIs:** replace the bodies of the `@tool` functions in `card_assist.py`.

## Security Note

This is a demo with a mock backend and no real authentication. The `make-payment` skill and
`AGENTS.md` enforce explicit confirmation before any payment, and the assistant never reveals
full card or bank numbers — but treat this as a teaching example, not production-ready code.

## Requirements

- Python 3.11+
- `ANTHROPIC_API_KEY` — for the main agent and the subagent
