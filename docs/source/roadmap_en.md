# Next-Stage Feature Roadmap

> **Planning status**: The capabilities described on this page are currently planned or under development and are not included in the current release. Final scope and release dates are subject to official release announcements.

In its next stage, EAI Simulator will expand its simulation and collaboration capabilities for dynamic environments shared by humans and machines. Planned work focuses on virtual humans, team collaboration, urban environments, and multi-robot navigation, enabling researchers to build more realistic and complex mixed human-robot experiments.

## Richer, More Natural Human Assets

The project plans to expand its virtual-human assets with a wider range of appearances and identities, together with social behaviors such as waving and gathering. Extension interfaces for conversational capabilities will provide a foundation for research into human-robot communication, collaboration, and group behavior.

## Team Planning for Dynamic Human-Robot Collaboration

Beyond the existing multi-robot discussion and task-allocation approach, EMOS, the project plans to introduce [TeamWeaver](https://github.com/southking372/TeamWeaver#teamweaver-hybrid-llm-and-optimization-based-planning-with-transparent-constraints-in-heterogeneous-multi-robot-teams) for dynamic human-robot collaboration.

TeamWeaver will use language models to understand task semantics and formulate planning constraints, then use mathematical optimization for concrete task assignment and adjustment. When humans join tasks temporarily, change the environment state, or create spatial conflicts with robots, the planned system will coordinate robot roles and actions from the latest state, with an emphasis on response efficiency, decision transparency, and traceability in dynamic scenarios.

## Interactive Social Urban Environments

The project plans to add a complete urban simulation environment with streetscapes, roads, pedestrians, and vehicles. The environment will include basic traffic organization and control, along with social traffic behaviors such as vehicles yielding to pedestrians. It will support research into urban roads, diverse traffic participants, and environments shared by people, vehicles, and robots.

## Cooperative Multi-Robot Navigation

The project plans to upgrade the existing navigation stack with db-CBS cooperative navigation for multi-robot scenarios, gradually replacing the current generic 2D global planner.

The new approach will jointly account for robot positions, goals, and path conflicts to plan coordinated routes. It is intended to reduce passing conflicts, mutual blockage, and local congestion while better supporting complex tasks with multiple robots operating simultaneously.
