## PersistentFlags not inherited in subcommands

In `command.go`, `PersistentFlags` set on the root command are not visible in child commands.

```go
package cmd

func init() {
    rootCmd.PersistentFlags().StringVar(&cfgFile, "config", "", "config file")
}
```

See `github.com/spf13/cobra` docs for expected behavior.
