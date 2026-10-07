# Navigation skill providers

This namespace is the upload target for providers that turn a navigation
`SkillRequest` into a path, trajectory, or velocity command. The concrete
providers are intentionally absent from this branch and will be supplied by
the dedicated Skill developer.

Future providers should be thin wrappers over those implementations and should
return `algorithm.skills.SkillPlan` records. They must not import a simulator
launcher or apply controller actions themselves.
