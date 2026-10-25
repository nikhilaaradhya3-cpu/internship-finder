import json
from uuid import uuid4
from datetime import datetime

from uagents import Context, Protocol
from uagents_core.contrib.protocols.chat import ChatMessage, TextContent
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

# True  = accept the payment without checking the ledger (demo shortcut).
# False = verify the transfer on the Fetch.ai ledger (needs cosmpy and the
#         buyer wallet in CommitPayment.metadata).
TEST_MODE = True

PRICE_FET = "0.1"
USE_TESTNET = True  # True = testnet (Dorado, test FET). False = mainnet, real FET.
FET_NETWORK = "stable-testnet" if USE_TESTNET else "mainnet"

payment_proto = Protocol(spec=payment_protocol_spec, role="seller")

_delivery_handler = None
_agent_wallet = None


def set_agent_wallet(wallet):
    """agent.py calls this with agent.wallet."""
    global _agent_wallet
    _agent_wallet = wallet


def set_delivery_handler(fn):
    """protocols.py calls this to say which function does the paid work.
    fn must be: async def fn(ctx, sender, text)"""
    global _delivery_handler
    _delivery_handler = fn


def _get(ctx: Context, key: str, default):
    try:
        raw = ctx.storage.get(key)
        return json.loads(raw) if raw else default
    except Exception:
        return default


def _set(ctx: Context, key: str, value) -> None:
    try:
        ctx.storage.set(key, json.dumps(value))
    except Exception as e:
        ctx.logger.error(f"PAYMENT: storage save failed: {type(e).__name__}: {e}")


def _text(msg: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.utcnow(),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=msg)],
    )


def get_recipient(ctx: Context) -> str:
    """Agent wallet (fetch1...) like the Fetch FET example; agent address as fallback."""
    try:
        if _agent_wallet is not None:
            return str(_agent_wallet.address())
    except Exception as e:
        ctx.logger.error(f"PAYMENT: wallet read failed: {type(e).__name__}: {e}")
    try:
        return str(ctx.agent.wallet.address())
    except Exception:
        pass
    return str(ctx.agent.address)


async def request_payment(ctx: Context, buyer: str, job: dict):
    """Send RequestPayment. The search runs only after CommitPayment is accepted."""
    recipient = get_recipient(ctx)
    ref = str(ctx.session)
    _set(ctx, f"pay_pending_{buyer}", {"job": job, "reference": ref})
    ctx.logger.info(f"PAYMENT: requesting {PRICE_FET} FET from {buyer[:14]}")
    ctx.logger.info(f"PAYMENT: recipient={recipient} network={FET_NETWORK}")

    try:
        request = RequestPayment(
            accepted_funds=[
                Funds(currency="FET", amount=PRICE_FET, payment_method="fet_direct")
            ],
            recipient=recipient,
            deadline_seconds=300,
            reference=ref,
            description="Live internship search with scam check",
            metadata={
                "agent": "internship-finder",
                "service": "internship_search",
                "fet_network": FET_NETWORK,
                "content": (
                    f"Please pay {PRICE_FET} FET to get live internship listings "
                    "ranked for you, with a scam check."
                ),
            },
        )
        ctx.logger.info(f"PAYMENT: sending {request}")
        await ctx.send(buyer, request)
        ctx.logger.info("PAYMENT: RequestPayment sent")
    except Exception as e:
        ctx.logger.error(f"PAYMENT: RequestPayment failed: {type(e).__name__}: {e}")
        _set(ctx, f"pay_pending_{buyer}", None)
        try:
            await ctx.send(
                buyer,
                _text("Sorry, I could not create the payment request. Please try again."),
            )
        except Exception:
            pass


def verify_fet_payment(tx_id: str, amount_fet: str, sender_wallet: str, recipient: str, logger) -> bool:
    try:
        from cosmpy.aerial.client import LedgerClient, NetworkConfig

        cfg = (
            NetworkConfig.fetchai_stable_testnet()
            if USE_TESTNET
            else NetworkConfig.fetchai_mainnet()
        )
        ledger = LedgerClient(cfg)
        tx = ledger.query_tx(tx_id)
        if not tx.is_successful():
            logger.error(f"PAYMENT: transaction {tx_id} was not successful")
            return False

        needed = int(float(amount_fet) * 10**18)
        denom = "atestfet" if USE_TESTNET else "afet"
        recipient_ok = amount_ok = sender_ok = False
        for event_type, attrs in tx.events.items():
            if event_type != "transfer":
                continue
            if attrs.get("recipient") == recipient:
                recipient_ok = True
                if attrs.get("sender") == sender_wallet:
                    sender_ok = True
                amount_str = attrs.get("amount", "")
                if amount_str.endswith(denom):
                    try:
                        if int(amount_str.replace(denom, "")) >= needed:
                            amount_ok = True
                    except ValueError:
                        pass
        logger.info(
            f"PAYMENT: verify recipient={recipient_ok} amount={amount_ok} sender={sender_ok}"
        )
        return recipient_ok and amount_ok and sender_ok
    except Exception as e:
        logger.error(f"PAYMENT: verification error: {type(e).__name__}: {e}")
        return False


@payment_proto.on_message(CommitPayment)
async def on_commit(ctx: Context, sender: str, msg: CommitPayment):
    ctx.logger.info(f"PAYMENT: CommitPayment received: {msg}")
    pending = _get(ctx, f"pay_pending_{sender}", None)
    seen = _get(ctx, "pay_seen_tx", [])

    if not pending or not msg.transaction_id:
        ctx.logger.info("PAYMENT: cancelled, no pending request or empty tx id")
        await ctx.send(
            sender,
            CancelPayment(transaction_id=msg.transaction_id, reason="No pending request"),
        )
        return
    if msg.transaction_id in seen:
        ctx.logger.info("PAYMENT: cancelled, transaction already used")
        await ctx.send(
            sender,
            CancelPayment(transaction_id=msg.transaction_id, reason="Transaction already used"),
        )
        return

    if TEST_MODE:
        verified = True
    else:
        buyer_wallet = None
        if isinstance(msg.metadata, dict):
            buyer_wallet = msg.metadata.get("buyer_fet_wallet") or msg.metadata.get(
                "buyer_fet_address"
            )
        if not buyer_wallet:
            ctx.logger.error("PAYMENT: missing buyer_fet_wallet in CommitPayment.metadata")
            verified = False
        else:
            verified = verify_fet_payment(
                msg.transaction_id, str(msg.funds.amount), buyer_wallet,
                get_recipient(ctx), ctx.logger,
            )

    if not verified:
        await ctx.send(
            sender,
            CancelPayment(transaction_id=msg.transaction_id, reason="Payment verification failed"),
        )
        return

    seen.append(msg.transaction_id)
    _set(ctx, "pay_seen_tx", seen[-500:])
    _set(ctx, f"pay_pending_{sender}", None)
    await ctx.send(sender, CompletePayment(transaction_id=msg.transaction_id))
    ctx.logger.info("PAYMENT: CompletePayment sent, delivering")

    if _delivery_handler is None:
        ctx.logger.error("PAYMENT: no delivery handler set")
        return
    try:
        await _delivery_handler(ctx, sender, pending["job"]["text"])
    except Exception as e:
        ctx.logger.error(f"PAYMENT: delivery failed: {type(e).__name__}: {e}")
        await ctx.send(sender, _text("Payment received but the search failed. Please try again."))


@payment_proto.on_message(RejectPayment)
async def on_reject(ctx: Context, sender: str, msg: RejectPayment):
    _set(ctx, f"pay_pending_{sender}", None)
    ctx.logger.info(f"PAYMENT: buyer rejected: {msg.reason}")
    await ctx.send(sender, _text("Payment declined, so I did not run the search."))
