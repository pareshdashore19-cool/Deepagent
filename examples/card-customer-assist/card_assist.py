#!/usr/bin/env python3
import warnings

warnings.filterwarnings("ignore", message="Core Pydantic V1 functionality")

"""
Card Customer Assist Agent

A credit card support chatbot ("Penny" for Northwind Bank) configured through
files on disk, mirroring the content-builder-agent example:

- AGENTS.md          -> persona, scope, and security rules (always-on memory)
- skills/*/SKILL.md  -> on-demand workflows (make a payment, view transactions)
- subagents.yaml     -> a delegated `spending-analyst` subagent
- mock tools below   -> a fake bank backend (in-memory account + transactions)

It supports exactly two things: MAKING A PAYMENT and VIEWING TRANSACTIONS.

This is an INTERACTIVE chat. State persists across turns via an in-memory
checkpointer, so multi-step flows (confirm a payment, etc.) work naturally.

Usage:
    uv run python card_assist.py
    # then chat: "show my recent transactions", "pay the minimum", "what did I spend on dining"

    # or run a one-shot message:
    uv run python card_assist.py "What's my balance and minimum due?"
"""

import asyncio
import os
import sys
import uuid
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

import yaml
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel

from deepagents import create_deep_agent
from deepagents.backends import FilesystemBackend

EXAMPLE_DIR = Path(__file__).parent
SESSIONS_DB = EXAMPLE_DIR / "card_sessions.db"
console = Console()


# ---------------------------------------------------------------------------
# MOCK BANK BACKEND
# A tiny in-memory "database" for a single signed-in customer. In a real app
# these tools would call your core banking / payments APIs. Here they just read
# and mutate the dicts below so you have something to try end to end.
# ---------------------------------------------------------------------------

# The customer is "already signed in" for this session.
CURRENT_CUSTOMER = "cust_001"

ACCOUNTS = {
    "cust_001": {
        "name": "Alex Morgan",
        "card_last4": "4827",
        "credit_limit": 10000.00,
        "current_balance": 2347.65,
        "statement_balance": 2105.40,
        "minimum_due": 75.00,
        "due_date": "2026-07-15",
        "autopay_enabled": False,
    }
}

# Credit cards the customer can pay (the payee side of a payment). Step 1 of the
# make-payment flow asks which of these to pay; the amount options come from here too.
PAYABLE_CARDS = {
    "cust_001": [
        {"id": "card_chase", "issuer": "Chase", "last4": "1234",
         "current_balance": 2347.65, "statement_balance": 2105.40, "minimum_due": 75.00, "due_date": "2026-07-15"},
        {"id": "card_discover", "issuer": "Discover", "last4": "3456",
         "current_balance": 980.12, "statement_balance": 980.12, "minimum_due": 35.00, "due_date": "2026-07-10"},
    ]
}

# Bank accounts the customer can pay FROM (funding sources). The customer can add
# more via the add-bank-account subagent, which appends to this list.
PAYMENT_METHODS = {
    "cust_001": [
        {"id": "pm_cap1", "nickname": "Capital One Checking", "bank_last4": "2456", "type": "checking"},
        {"id": "pm_bofa", "nickname": "Bank of America Checking", "bank_last4": "6474", "type": "checking"},
    ]
}

TRANSACTIONS = {
    "cust_001": [
        {"id": "txn_1042", "date": "2026-06-24", "merchant": "Blue Bottle Coffee", "category": "Dining", "amount": 6.75, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1041", "date": "2026-06-23", "merchant": "Whole Foods Market", "category": "Groceries", "amount": 87.32, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1040", "date": "2026-06-22", "merchant": "Uber", "category": "Travel", "amount": 23.40, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1039", "date": "2026-06-21", "merchant": "Amazon", "category": "Shopping", "amount": 154.18, "status": "posted", "location": "Online"},
        {"id": "txn_1038", "date": "2026-06-20", "merchant": "Pacific Gas & Electric", "category": "Bills", "amount": 142.06, "status": "posted", "location": "Online"},
        {"id": "txn_1037", "date": "2026-06-19", "merchant": "Tartine Bakery", "category": "Dining", "amount": 18.90, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1036", "date": "2026-06-18", "merchant": "Shell", "category": "Travel", "amount": 54.10, "status": "posted", "location": "Oakland, CA"},
        {"id": "txn_1035", "date": "2026-06-17", "merchant": "Netflix", "category": "Bills", "amount": 15.49, "status": "posted", "location": "Online"},
        {"id": "txn_1034", "date": "2026-06-16", "merchant": "Trader Joe's", "category": "Groceries", "amount": 63.77, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1033", "date": "2026-06-15", "merchant": "SFMOMA", "category": "Entertainment", "amount": 25.00, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1032", "date": "2026-06-14", "merchant": "Sweetgreen", "category": "Dining", "amount": 16.45, "status": "posted", "location": "San Francisco, CA"},
        {"id": "txn_1031", "date": "2026-06-13", "merchant": "Delta Air Lines", "category": "Travel", "amount": 412.30, "status": "posted", "location": "Online"},
    ]
}

PAYMENT_LOG: list[dict] = []


@tool
def get_card_summary() -> dict:
    """Get the signed-in customer's credit card summary.

    Returns the card (last 4 only), current balance, statement balance,
    minimum payment due, due date, credit limit, and available credit.
    Use this before helping with a payment or when the customer asks what they owe.
    """
    acct = ACCOUNTS[CURRENT_CUSTOMER]
    return {
        "name": acct["name"],
        "card_last4": acct["card_last4"],
        "current_balance": round(acct["current_balance"], 2),
        "statement_balance": round(acct["statement_balance"], 2),
        "minimum_due": round(acct["minimum_due"], 2),
        "due_date": acct["due_date"],
        "credit_limit": round(acct["credit_limit"], 2),
        "available_credit": round(acct["credit_limit"] - acct["current_balance"], 2),
        "autopay_enabled": acct["autopay_enabled"],
    }


@tool
def list_payable_cards() -> dict:
    """List the customer's credit cards that can receive a payment.

    Use this FIRST when starting a payment so the customer can choose which card to
    pay. Returns each card's issuer and last 4 only, with its balance and minimum due.
    """
    return {
        "cards": [
            {
                "id": c["id"],
                "issuer": c["issuer"],
                "last4": c["last4"],
                "current_balance": round(c["current_balance"], 2),
                "minimum_due": round(c["minimum_due"], 2),
                "due_date": c["due_date"],
            }
            for c in PAYABLE_CARDS[CURRENT_CUSTOMER]
        ]
    }


@tool
def get_payment_amount_options(card_id: str) -> dict:
    """List the standard payment amounts for one of the customer's cards.

    Call this after the customer has chosen which card to pay, to offer the usual
    choices (minimum due, statement balance, pay in full) plus a custom amount.

    Args:
        card_id: Which card to pay, from list_payable_cards (e.g. "card_chase").
    """
    cards = {c["id"]: c for c in PAYABLE_CARDS[CURRENT_CUSTOMER]}
    if card_id not in cards:
        return {"error": f"Unknown card {card_id!r}. Call list_payable_cards first."}
    card = cards[card_id]
    return {
        "card_issuer": card["issuer"],
        "card_last4": card["last4"],
        "due_date": card["due_date"],
        "options": [
            {"label": "Minimum due", "amount": round(card["minimum_due"], 2)},
            {"label": "Statement balance", "amount": round(card["statement_balance"], 2)},
            {"label": "Current balance (pay in full)", "amount": round(card["current_balance"], 2)},
            {"label": "Custom amount", "amount": None},
        ],
    }


@tool
def list_transactions(
    limit: int = 10,
    category: Optional[str] = None,
    search: Optional[str] = None,
) -> dict:
    """List the signed-in customer's recent card transactions, newest first.

    Args:
        limit: Maximum number of transactions to return (default 10).
        category: Optional filter, e.g. "Dining", "Groceries", "Travel", "Bills",
            "Shopping", "Entertainment". Case-insensitive.
        search: Optional case-insensitive substring match on the merchant name.

    Returns:
        A dict with the matching transactions and how many were returned.
    """
    txns = TRANSACTIONS[CURRENT_CUSTOMER]
    if category:
        txns = [t for t in txns if t["category"].lower() == category.lower()]
    if search:
        txns = [t for t in txns if search.lower() in t["merchant"].lower()]
    txns = txns[: max(0, limit)]
    return {"count": len(txns), "transactions": txns}


@tool
def get_transaction(transaction_id: str) -> dict:
    """Get full details for one transaction by its id (e.g. "txn_1039").

    Use when the customer asks "what is this charge?" about a specific transaction.
    Returns an error dict if the id is not found.
    """
    for t in TRANSACTIONS[CURRENT_CUSTOMER]:
        if t["id"] == transaction_id:
            return t
    return {"error": f"No transaction found with id {transaction_id!r}"}


@tool
def list_payment_methods() -> dict:
    """List the bank accounts the customer can pay from (nickname + last 4 only).

    Use this before processing a payment so the customer can choose a source.
    """
    return {"payment_methods": PAYMENT_METHODS[CURRENT_CUSTOMER]}


@tool
def add_bank_account(
    account_number: str,
    routing_number: str,
    account_type: str = "checking",
    nickname: Optional[str] = None,
) -> dict:
    """Link a new bank account the customer can pay from.

    This tool belongs to the add-bank-account subagent. It validates the routing and
    account numbers, stores the account, and returns the new funding source by
    nickname and last 4 ONLY — it never returns or logs the full numbers.

    Args:
        account_number: The customer's bank account number (digits).
        routing_number: The 9-digit ABA routing number.
        account_type: "checking" or "savings" (default "checking").
        nickname: Optional friendly name; a default is generated from the last 4 if omitted.

    Returns:
        On success: {"added": True, ...} with the new method's id, nickname, and last 4.
        On failure: a dict with an "error" describing what was invalid.
    """
    acct_digits = "".join(ch for ch in str(account_number) if ch.isdigit())
    rout_digits = "".join(ch for ch in str(routing_number) if ch.isdigit())
    if not (4 <= len(acct_digits) <= 17):
        return {"error": "Account number looks invalid — it should be 4 to 17 digits."}
    if len(rout_digits) != 9:
        return {"error": "Routing number must be exactly 9 digits."}
    if account_type not in ("checking", "savings"):
        return {"error": "Account type must be 'checking' or 'savings'."}

    last4 = acct_digits[-4:]
    for m in PAYMENT_METHODS[CURRENT_CUSTOMER]:
        if m["bank_last4"] == last4 and m["type"] == account_type:
            return {"error": f"A {account_type} account ending in {last4} is already linked."}

    method = {
        "id": f"pm_{uuid.uuid4().hex[:6]}",
        "nickname": nickname or f"{account_type.capitalize()} ••{last4}",
        "bank_last4": last4,
        "type": account_type,
    }
    PAYMENT_METHODS[CURRENT_CUSTOMER].append(method)
    return {
        "added": True,
        "id": method["id"],
        "nickname": method["nickname"],
        "bank_last4": last4,
        "type": account_type,
    }


@tool
def make_payment(
    card_id: str,
    payment_method_id: str,
    amount: float,
    payment_date: str,
) -> dict:
    """Process a payment toward one of the customer's credit cards.

    ONLY call this after the customer has explicitly confirmed ALL of: the card to
    pay, the funding account, the amount, and the payment date. Never retry on failure.

    Args:
        card_id: Which card to pay, from list_payable_cards (e.g. "card_chase").
        payment_method_id: Funding account to pay from, from list_payment_methods (e.g. "pm_cap1").
        amount: Dollar amount to pay. Must be greater than 0.
        payment_date: Date to make the payment, ISO format "YYYY-MM-DD". Must be today
            or later, and no more than 90 days from today.

    Returns:
        On success: a confirmation with a reference number, scheduled date, and new balance.
        On failure: a dict with an "error" describing why.
    """
    cards = {c["id"]: c for c in PAYABLE_CARDS[CURRENT_CUSTOMER]}
    if card_id not in cards:
        return {"error": f"Unknown card {card_id!r}. Call list_payable_cards first."}
    card = cards[card_id]

    methods = {m["id"]: m for m in PAYMENT_METHODS[CURRENT_CUSTOMER]}
    if payment_method_id not in methods:
        return {"error": f"Unknown payment method {payment_method_id!r}. Call list_payment_methods first."}

    if amount is None or amount <= 0:
        return {"error": "Payment amount must be greater than $0."}
    # Simple guardrail to catch fat-finger errors.
    if amount > 50000:
        return {"error": f"Amount ${amount:,.2f} exceeds the $50,000 per-payment limit; please confirm a smaller amount."}

    # Validate the payment date: must parse, be today or later, within 90 days.
    try:
        pay_date = datetime.strptime(payment_date, "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return {"error": f"Could not read payment date {payment_date!r}. Use the format YYYY-MM-DD."}
    today = datetime.now().date()
    if pay_date < today:
        return {"error": f"Payment date {pay_date.isoformat()} is in the past. Choose today or a future date."}
    if pay_date > today + timedelta(days=90):
        return {"error": f"Payment date {pay_date.isoformat()} is more than 90 days out. Choose a date within 90 days of today."}

    card["current_balance"] = round(card["current_balance"] - amount, 2)
    confirmation = f"NWB-{datetime.now():%Y%m%d}-{uuid.uuid4().hex[:6].upper()}"
    record = {
        "confirmation_number": confirmation,
        "card_issuer": card["issuer"],
        "card_last4": card["last4"],
        "amount": round(amount, 2),
        "paid_from": methods[payment_method_id]["nickname"],
        "paid_from_last4": methods[payment_method_id]["bank_last4"],
        "payment_date": pay_date.isoformat(),
        "new_balance": card["current_balance"],
        "status": "completed" if pay_date == today else "scheduled",
        "timestamp": datetime.now().isoformat(timespec="seconds"),
    }
    PAYMENT_LOG.append(record)
    return record


# ---------------------------------------------------------------------------
# AGENT ASSEMBLY (same pattern as content-builder-agent)
# ---------------------------------------------------------------------------

def load_subagents(config_path: Path) -> list:
    """Load subagent definitions from YAML and wire up tools by name.

    Subagents aren't loaded from files natively by deepagents (memory and skills
    are) — this small helper externalizes them to YAML to keep config out of code.
    """
    available_tools = {
        "list_transactions": list_transactions,
        "get_transaction": get_transaction,
        "get_card_summary": get_card_summary,
        "add_bank_account": add_bank_account,
    }

    with open(config_path) as f:
        config = yaml.safe_load(f)

    subagents = []
    for name, spec in config.items():
        subagent = {
            "name": name,
            "description": spec["description"],
            "system_prompt": spec["system_prompt"],
        }
        if "model" in spec:
            subagent["model"] = spec["model"]
        if "tools" in spec:
            subagent["tools"] = [available_tools[t] for t in spec["tools"]]
        subagents.append(subagent)
    return subagents


def create_card_assistant(checkpointer):
    """Create the credit card support agent, configured by files on disk.

    The checkpointer persists conversation state keyed by thread_id (our
    session_id), so a session can be resumed across turns — and, with a
    database-backed checkpointer, across process restarts.
    """
    return create_deep_agent(
        model=ChatAnthropic(model_name="claude-sonnet-4-6"),
        memory=["./AGENTS.md"],                                  # persona + security rules
        skills=["./skills/"],                                    # make-payment, view-transactions
        tools=[                                                  # the mock bank backend
            get_card_summary,
            list_payable_cards,
            get_payment_amount_options,
            list_transactions,
            get_transaction,
            list_payment_methods,
            make_payment,
            # NOTE: add_bank_account is intentionally NOT here — it lives only on the
            # add-bank-account subagent, so adding a funding source always delegates.
        ],
        subagents=load_subagents(EXAMPLE_DIR / "subagents.yaml"),  # spending-analyst
        backend=FilesystemBackend(root_dir=EXAMPLE_DIR, virtual_mode=False),
        checkpointer=checkpointer,                               # persist chat across turns
    )


# ---------------------------------------------------------------------------
# DISPLAY
# ---------------------------------------------------------------------------

def print_message(msg) -> None:
    """Pretty-print one message from the stream."""
    if isinstance(msg, AIMessage):
        content = msg.content
        if isinstance(content, list):
            content = "\n".join(p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text")
        if content and content.strip():
            console.print(Panel(Markdown(content), title="Penny", border_style="green"))

        for tc in msg.tool_calls or []:
            name = tc.get("name", "")
            args = tc.get("args", {})
            if name == "task":
                console.print(f"  [magenta]>> delegating to {args.get('subagent_type', 'subagent')}[/]")
            elif name == "make_payment":
                console.print(
                    f"  [bold yellow]>> paying ${args.get('amount', '?')} to {args.get('card_id', '?')} "
                    f"from {args.get('payment_method_id', '?')} on {args.get('payment_date', '?')}[/]"
                )
            elif name in ("get_card_summary", "list_payable_cards", "get_payment_amount_options",
                          "list_transactions", "get_transaction", "list_payment_methods"):
                console.print(f"  [dim]>> {name}({', '.join(f'{k}={v!r}' for k, v in args.items())})[/]")

    elif isinstance(msg, ToolMessage):
        if getattr(msg, "name", "") == "make_payment":
            tag = "[green]✓ payment processed[/]" if "confirmation_number" in str(msg.content) else "[red]✗ payment not processed[/]"
            console.print(f"  {tag}")


async def run_turn(agent, text: str, thread_id: str, printed_count: int) -> int:
    """Stream one user turn, printing only newly-produced messages."""
    async for chunk in agent.astream(
        {"messages": [("user", text)]},
        config={"configurable": {"thread_id": thread_id}},
        stream_mode="values",
    ):
        messages = chunk.get("messages", [])
        for msg in messages[printed_count:]:
            print_message(msg)
        printed_count = max(printed_count, len(messages))
    return printed_count


async def main() -> None:
    # One SQLite file stores every session's conversation, keyed by session_id.
    # The app drives the agent with astream(), so we need the ASYNC saver
    # (the sync SqliteSaver raises NotImplementedError on async calls).
    # from_conn_string is an async context manager that owns the aiosqlite connection.
    async with AsyncSqliteSaver.from_conn_string(str(SESSIONS_DB)) as checkpointer:
        await checkpointer.setup()  # idempotent: creates the checkpoint tables if absent
        agent = create_card_assistant(checkpointer)

        # A random session_id per run is the conversation's key in the database.
        # Resume an earlier conversation by passing CARD_SESSION_ID=<id>.
        session_id = os.environ.get("CARD_SESSION_ID") or f"sess-{uuid.uuid4()}"
        thread_id = session_id
        printed_count = 0

        acct = ACCOUNTS[CURRENT_CUSTOMER]
        console.print()
        console.print("[bold blue]Northwind Bank — Card Assistant (Penny)[/]")
        console.print(f"[dim]Signed in as {acct['name']} · card ••{acct['card_last4']}[/]")
        console.print(f"[dim]Session: {session_id}  ·  stored in {SESSIONS_DB.name}[/]")

        # One-shot mode if a message was passed on the command line.
        if len(sys.argv) > 1:
            text = " ".join(sys.argv[1:])
            console.print(Panel(text, title="You", border_style="blue"))
            # Count the user message we're about to send so we don't reprint it.
            await run_turn(agent, text, thread_id, printed_count + 1)
            return

        console.print("[dim]Try: \"show my recent transactions\" · \"pay the minimum\" · "
                      "\"what did I spend on dining\" · \"what is charge txn_1031\"[/]")
        console.print("[dim]Type 'quit' or 'exit' to leave.[/]")
        console.print()

        while True:
            try:
                text = console.input("[bold blue]You:[/] ").strip()
            except (EOFError, KeyboardInterrupt):
                break
            if not text:
                continue
            if text.lower() in {"quit", "exit", "q"}:
                break
            # +1 accounts for the human message that gets added to state this turn.
            printed_count = await run_turn(agent, text, thread_id, printed_count + 1)

        console.print("\n[dim]Goodbye![/]")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted[/]")
