from uagents import Agent

from protocols import internship_protocol, scam_result_protocol
from payment_proto import payment_proto, set_agent_wallet

agent = Agent()

agent.include(internship_protocol, publish_manifest=True)
agent.include(scam_result_protocol)
agent.include(payment_proto, publish_manifest=True)

try:
    set_agent_wallet(agent.wallet)
except Exception as e:
    print(f"PAYMENT: could not read agent wallet: {type(e).__name__}: {e}")

if __name__ == "__main__":
    agent.run()
