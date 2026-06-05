## Validation tag `required` fails with empty string

Error from `validator.go`:

```
Key: '' Error:Field validation for 'Email' failed on the 'required' tag
```

Import path: `github.com/go-playground/validator/v10`

The `ValidateStruct` helper in package validator should treat empty strings consistently.
