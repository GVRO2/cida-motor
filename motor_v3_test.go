package main

import (
	"os"
	"path/filepath"
	"testing"
)

func TestGetB16ID(t *testing.T) {
	tests := []struct {
		n        int
		expected string
	}{
		{0, "A"},
		{5, "F"},
		{6, "A0"},
		{101, "FF"},
		{102, "A00"},
	}

	for _, tt := range tests {
		t.Run(string(rune(tt.n)), func(t *testing.T) {
			result := getB16ID(tt.n)
			if result != tt.expected {
				t.Errorf("getB16ID(%d) = %s; want %s", tt.n, result, tt.expected)
			}
		})
	}
}

func TestEhArquivoDeTeste(t *testing.T) {
	tests := []struct {
		root      string
		file      string
		pastaOrig string
		expected  bool
	}{
		{filepath.Join("src", "test", "java"), "App.java", "src", true},
		{filepath.Join("src", "main", "java"), "AppTest.java", "src", true},
		{filepath.Join("src", "main", "java"), "App.java", "src", false},
	}

	for _, tt := range tests {
		t.Run(tt.file, func(t *testing.T) {
			result := ehArquivoDeTeste(tt.root, tt.file, tt.pastaOrig)
			if result != tt.expected {
				t.Errorf("ehArquivoDeTeste(%s, %s, %s) = %t; want %t", tt.root, tt.file, tt.pastaOrig, result, tt.expected)
			}
		})
	}
}

func TestIsBinaryFileGo(t *testing.T) {
	// Create a temporary text file
	tmpText, err := os.CreateTemp("", "test_text_*.txt")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(tmpText.Name())
	tmpText.WriteString("This is normal text content.")
	tmpText.Close()

	// Create a temporary binary file with null byte
	tmpBin, err := os.CreateTemp("", "test_bin_*.bin")
	if err != nil {
		t.Fatal(err)
	}
	defer os.Remove(tmpBin.Name())
	tmpBin.Write([]byte{0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 0x00, 0x00})
	tmpBin.Close()

	tests := []struct {
		name     string
		path     string
		expected bool
	}{
		{"Text File", tmpText.Name(), false},
		{"Binary File", tmpBin.Name(), true},
		{"Binary Extension .png", "logo.png", true},
		{"Text Extension .java", "App.java", false},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := isBinaryFileGo(tt.path)
			if result != tt.expected {
				t.Errorf("isBinaryFileGo(%s) = %t; want %t", tt.path, result, tt.expected)
			}
		})
	}
}

func TestResolveValidationLevel(t *testing.T) {
	tests := []struct {
		name                    string
		validationLevel         string
		strictValidation        bool
		validationLevelExplicit bool
		expected                string
		expectErr               bool
	}{
		{"default balanced", "balanced", false, false, "balanced", false},
		{"explicit balanced", "balanced", false, true, "balanced", false},
		{"explicit strict", "strict", false, true, "strict", false},
		{"strict alias", "balanced", true, false, "strict", false},
		{"strict alias with explicit strict", "strict", true, true, "strict", false},
		{"strict alias conflicts with balanced", "balanced", true, true, "", true},
		{"invalid level", "unknown", false, true, "", true},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result, err := resolveValidationLevel(tt.validationLevel, tt.strictValidation, tt.validationLevelExplicit)
			if tt.expectErr && err == nil {
				t.Fatal("expected error")
			}
			if !tt.expectErr && err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if result != tt.expected {
				t.Errorf("resolveValidationLevel() = %s; want %s", result, tt.expected)
			}
		})
	}
}
