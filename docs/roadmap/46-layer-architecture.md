# 46-Layer Architecture Roadmap

The 46-layer architecture is valuable as a long-range map, but it is not the current runtime source of truth.

Current source of truth: the modular `brain/runtime/tools` system.

Use the 46-layer material as a backlog of ideas. Promote an idea into runtime only when it can be expressed as a small module with tests, policy boundaries, and a clear owner.

## Migration Rule

Do not add a new root-level subsystem just because a layer exists in the roadmap. First answer:

- Which existing module owns it?
- What input/output contract does it expose?
- What is the smallest useful behavior?
- What test proves it works?
- What policy or approval gate protects it?

## Preserved Local Archive

The previous experimental 46-layer files were moved locally to:

```text
_archive/46-layer-experiment/
```

That folder is intentionally ignored by git so generated data, experiments, and large scratch files do not pollute the production branch.
