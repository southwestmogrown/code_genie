from crewai import Agent
from crewai.project import CrewBase, agent
from crewai.agents.agent_builder.base_agent import BaseAgent


@CrewBase
class CoderReviewerCrew():
    """Agent factory for the coder and reviewer agents."""

    agents: list[BaseAgent]

    agents_config = 'config/agents.yaml'

    @agent
    def coder(self) -> Agent:
        return Agent(
            config=self.agents_config['coder'],  # type: ignore[index]
            verbose=True,
        )

    @agent
    def reviewer(self) -> Agent:
        return Agent(
            config=self.agents_config['reviewer'],  # type: ignore[index]
            verbose=True,
        )
