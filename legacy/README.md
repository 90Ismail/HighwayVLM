# Legacy Compatibility Shims

`legacy/compat/` holds wrappers from the previous project layout.

They forward imports to `highwayvlm/` and are kept only for backward compatibility.
New code should import directly from `highwayvlm/` or use scripts in `scripts/`.
