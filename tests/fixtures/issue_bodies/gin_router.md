## Panic in BindJSON when context is nil

Reproduction in `context.go` when calling `BindJSON` on a nil `*Context`.

```
panic: runtime error: invalid memory address or nil pointer dereference
```

The handler in `router/router.go` should guard before binding.
Package middleware may also need a fix.
