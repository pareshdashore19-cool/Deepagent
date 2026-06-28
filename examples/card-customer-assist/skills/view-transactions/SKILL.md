---
name: view-transactions
description: Use when the customer asks about activity on their card — "show my recent transactions", "what did I spend", "what is this charge from <merchant>", "did a payment go through", "how much did I spend on dining". Covers listing activity, explaining a specific charge, and summarizing spending.
---

# View Transactions Skill

Your job: help the customer understand the activity on their card. You have **read-only**
tools here — you are never changing anything in this skill.

## Choose the right path

### A. "Show my recent transactions" / "what's on my card"

1. Call `list_transactions(limit=10)` (raise the limit if they ask for more).
2. Present a tidy table or bullet list: **date · merchant · category · amount**.
3. Show charges as positive amounts and payments/credits clearly marked (e.g. "− $200.00 payment").
4. Offer a follow-up: "Want details on any of these, or a spending summary?"

### B. "What is this charge?" / details on one transaction

1. If you can identify the transaction from the recent list, call
   `get_transaction(transaction_id="...")` for the full details.
2. Share: merchant, date, amount, category, status, and any merchant location/description.
3. If the customer doesn't recognize it, do **not** open a dispute yourself (out of scope).
   Reassure them and tell them to call the number on the back of their card to dispute it.

### C. "How much did I spend on X" / "summarize my spending" / "break it down"

This is an **analysis** question. Delegate it to the `spending-analyst` subagent:

```
task(
    subagent_type="spending-analyst",
    description="Summarize how much the customer spent on dining in their recent transactions."
)
```

- Pass the customer's actual question in the description (period, category, merchant).
- When it returns, relay the summary to the customer in your own warm, concise voice.
- Use the subagent for *aggregation/insight*, not for a plain "list my transactions" request
  (handle that yourself with path A).

## Formatting

- Currency as `$1,234.56`; dates as "July 3, 2026".
- Keep tables narrow and scannable. Don't dump raw tool JSON at the customer.
- Categories in plain words: Dining, Groceries, Travel, Shopping, Bills, etc.

## Quality Checklist

- [ ] Used the right path (list vs. detail vs. analysis)
- [ ] Delegated aggregation questions to `spending-analyst`
- [ ] Never invented amounts — every number traces to a tool result
- [ ] Offered a relevant next step
