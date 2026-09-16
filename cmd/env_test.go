package cmd

import "testing"

func TestBooleanEnvironmentOverridesDefault(t *testing.T) {
	old := flags.DisableAutoUpdate
	t.Cleanup(func() { flags.DisableAutoUpdate = old })
	for _, tc := range []struct {
		value string
		want  bool
	}{{"false", false}, {"0", false}, {"true", true}, {"1", true}, {"FALSE", false}} {
		t.Run(tc.value, func(t *testing.T) {
			flags.DisableAutoUpdate = !tc.want
			t.Setenv("AGENT_DISABLE_AUTO_UPDATE", tc.value)
			loadFromEnv()
			if flags.DisableAutoUpdate != tc.want {
				t.Fatalf("environment %q: got %v, want %v", tc.value, flags.DisableAutoUpdate, tc.want)
			}
		})
	}
}
