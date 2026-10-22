from uagents import Agent

from protocols import internship_protocol

agent = Agent()

agent.include(internship_protocol, publish_manifest=True)

if __name__ == "__main__":
    agent.run()
