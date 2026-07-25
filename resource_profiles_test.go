package main

import "testing"

func TestWorkersForProfile(t *testing.T) {
	tests := []struct {
		name     string
		profile  string
		cpus     int
		expected int
	}{
		{"light one cpu", "light", 1, 1},
		{"light two cpus", "light", 2, 1},
		{"light four cpus", "light", 4, 2},
		{"light sixteen cpus", "light", 16, 4},
		{"medium one cpu", "medium", 1, 2},
		{"medium eight cpus", "medium", 8, 8},
		{"medium sixteen cpus", "medium", 16, 10},
		{"hard one cpu", "hard", 1, 10},
		{"hard eight cpus", "hard", 8, 16},
		{"hard sixty four cpus", "hard", 64, 64},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			got, err := workersForProfile(tt.profile, tt.cpus)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tt.expected {
				t.Fatalf("workersForProfile(%q, %d) = %d; want %d", tt.profile, tt.cpus, got, tt.expected)
			}
		})
	}
}

func TestResolveResourceSettings(t *testing.T) {
	settings, err := resolveResourceSettings("", false, 0, false, func() int { return 16 }, func() int { return 16 })
	if err != nil {
		t.Fatal(err)
	}
	if settings.EffectiveWorkers != 10 || settings.Profile != "default" || settings.ResolutionSource != "default" {
		t.Fatalf("unexpected default settings: %+v", settings)
	}

	settings, err = resolveResourceSettings("hard", true, 8, true, func() int { return 16 }, func() int { return 16 })
	if err != nil {
		t.Fatal(err)
	}
	if settings.EffectiveWorkers != 8 || settings.Profile != "hard" || settings.ResolutionSource != "explicit_workers" {
		t.Fatalf("explicit workers did not override profile: %+v", settings)
	}
	if settings.RequestedWorkers == nil || *settings.RequestedWorkers != 8 {
		t.Fatalf("requested workers not recorded: %+v", settings)
	}
}

func TestResolveResourceSettingsRejectsInvalidValues(t *testing.T) {
	for _, workers := range []int{0, -1, 257} {
		t.Run("workers", func(t *testing.T) {
			if _, err := resolveResourceSettings("", false, workers, true, func() int { return 8 }, func() int { return 8 }); err == nil {
				t.Fatalf("expected invalid workers %d to fail", workers)
			}
		})
	}

	if _, err := resolveResourceSettings("unknown", true, 0, false, func() int { return 8 }, func() int { return 8 }); err == nil {
		t.Fatal("expected invalid profile to fail")
	}
}
