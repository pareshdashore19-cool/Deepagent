---
name: make-payment
description: Use when the customer wants to pay a credit card bill — e.g. "make a payment", "pay my balance", "pay the minimum", "pay $200", "settle my card". Walks through choosing the card, the funding account, the amount, and the date, confirming, and processing the payment safely.
---

# Make a Payment Skill

Your job: help the customer pay a credit card bill **safely and with explicit confirmation**.
Never move money until the customer has confirmed the **card**, the **funding account**, the
**amount**, AND the **date**.

## How to run this flow (strict)

Do the steps **strictly one at a time, in order (1 → 2 → 3 → 4 → 5)**. Each step gathers
exactly one decision. Rules:

- **Ask for only one thing per turn.** Do not ask for the amount and the account in the same
  message, etc.
- **Do not advance to the next step until the current step's decision is settled** by the
  customer. In particular, do **not** call a later step's tool early — e.g. do not call
  `get_payment_amount_options` until a funding account is chosen.
- **Do not bundle questions or look ahead.** If you have the card but not the account, your
  next message is about the account only — not the amount.
- If the customer volunteers a later detail early (e.g. names an amount during Step 1), you
  may note it, but still walk the steps in order and confirm it when you reach that step.

## Step 1 — Which card to pay

Call `list_payable_cards()` and show the customer their cards by **issuer and last 4 only**
(e.g. "Chase ••1234" and "Discover ••3456"), with the balance and minimum due for each.
Ask which card they'd like to pay. If they already named one, confirm you have the right card.

**Gate:** do not move to Step 2 until the customer has confirmed which card to pay.

## Step 1.5 — Check for autopay or a pending payment

As soon as the card is settled, call `check_payment_eligibility(card_id="<id>")` **before**
asking for the funding account or amount.

- If it returns `{"clear": True}`, there's nothing flagged — continue to Step 2.
- If it returns `{"clear": False, "needs_confirmation": True, ...}`, the card has **autopay**
  turned on or a **payment already pending**, so a manual payment could post on top of it.
  Show the customer the `warning` from the result and ask whether they still want to proceed:
  - If they say **yes**, continue to Step 2 as normal.
  - If they say **no**, stop the payment flow for that card (offer another card if they have one).

**Gate:** do not move to Step 2 until either the result was `clear: True`, or the customer
explicitly confirmed they want to proceed despite the autopay/pending-payment warning.

## Step 2 — How they'd like to pay (the funding account)

The customer pays **from** a bank account. Always **offer both paths together** so the
customer knows adding a new account is an option — don't wait for them to ask:

Call `list_payment_methods()`, list the accounts on file by nickname and last 4 (e.g.
"Capital One Checking ••2456"), and then explicitly present the choice, e.g.:

> "Which account would you like to pay from — one of these, or would you like to **add a
> new bank account**?"

**(a) Use an account already on file.** If they pick one of the listed accounts, use it.

**(b) Add a new bank account.** If they choose to add one (or name an account that isn't on
file), you must **delegate to the `add-bank-account` subagent** — you cannot link an account
yourself. First collect, in the conversation:

- their **bank account number**, and
- their **9-digit routing number**.

Then call the subagent via the `task` tool with `subagent_type="add-bank-account"`, putting
the account number and routing number in the task description. When it returns, the new
account is on file — use the nickname and last 4 it reports. Refer to the account by its
**last 4 only**; never repeat the full numbers back.

> The customer can also ask to "add a bank account" on its own, outside a payment. Handle it
> the same way: collect the two numbers, delegate to `add-bank-account`, confirm by last 4.

**Gate:** do not move to Step 3 (and do not call `get_payment_amount_options`) until a
funding account is settled — either chosen from the list or successfully added.

## Step 3 — The amount

Now that you know the card, call `get_payment_amount_options(card_id="<id>")` and offer the
standard choices plus a custom amount:

- **Minimum due** — avoids a late fee
- **Statement balance** — avoids interest charges
- **Current balance (pay in full)** — pays the card off
- **A custom amount** they specify

Rules:
- If the customer names a number, use it exactly. Do not round or change it.
- The amount must be greater than $0.
- If the amount is **more than the card's current balance**, flag the overpayment and
  confirm they really want to before continuing.

**Gate:** do not move to Step 4 until the amount is settled.

## Step 4 — The payment date

Ask **when** they want the payment made. Rules:

- The date must be **today or later**, and **no more than 90 days from today**.
- If the customer gives a date more than 90 days out (or in the past), explain the limit
  and ask for a date within range. Do not call `make_payment` with an out-of-range date.
- Convert relative phrasing ("the 15th", "next Friday", "the due date") into an explicit
  `YYYY-MM-DD` date and read it back to the customer.

**Gate:** do not move to Step 5 until the date is settled.

## Step 5 — Confirm, then process

**Confirm out loud (REQUIRED before charging).** Restate everything and ask for a clear yes:

> "Just to confirm: pay **$X** toward your **<issuer> ••<last4>** card, from
> **<account nickname> ••<last4>**, on **<date>**. Shall I go ahead?"

**Do NOT call `make_payment` until the customer explicitly says yes** to this exact card,
account, amount, and date. A vague "ok" earlier in the chat does not count.

Once confirmed, call:

```
make_payment(card_id="<id>", payment_method_id="<id>", amount=<number>, payment_date="YYYY-MM-DD")
```

Then:
- On success: confirm with the **amount**, the **confirmation number**, the **payment date**,
  and the **new balance** from the tool result. Offer a short next step.
- On failure: explain the reason in plain language (bad date, unknown account, amount too
  large). **Do not retry automatically.** Ask the customer how they'd like to proceed.

## Guardrails Recap

- [ ] Customer chose the card from `list_payable_cards()`
- [ ] `check_payment_eligibility()` was called; if it flagged autopay/pending, the customer confirmed they still want to proceed
- [ ] Funding account is on file (via `list_payment_methods()`) or was added via the `add-bank-account` subagent
- [ ] Amount is explicit and > $0 (and confirmed if it exceeds the balance)
- [ ] Date is today–90 days out, given as `YYYY-MM-DD`
- [ ] Customer explicitly confirmed card, account, amount, AND date
- [ ] Reported the confirmation number, date, and new balance after success
