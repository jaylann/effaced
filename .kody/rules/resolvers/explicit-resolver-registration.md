---
title: "Resolver registration stays explicit — no auto-discovery"
scope: "file"
path:
  - "packages/effaced/src/effaced/resolvers/**"
  - "packages/effaced-stripe/src/effaced_stripe/**"
severity_min: "medium"
buckets: ["resolver-contract"]
enabled: true
---
## Instructions
Resolver registration stays explicit — the caller names every resolver that may
touch subject data. Flag any auto-discovery mechanism (entry-points, plugin
scanning, import-time side-effect registration) that would erase or export data
through a resolver the caller never named.

## Examples
### Bad example
```python
def load_resolvers() -> list[Resolver]:
    # Any installed package advertising the entry point silently joins erasure.
    return [ep.load()() for ep in entry_points(group="effaced.resolvers")]
```
### Good example
```python
registry = ResolverRegistry()
registry.register(StripeResolver(client))   # explicit, caller-named
```
