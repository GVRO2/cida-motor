package main

import (
	"context"
	"crypto/sha256"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

func getB16ID(n int) string {
	prefixChars := "ABCDEF"
	allChars := "0123456789ABCDEF"

	if n < len(prefixChars) {
		return string(prefixChars[n])
	}

	n -= len(prefixChars)
	if n < 6*16 {
		return string(prefixChars[n/16]) + string(allChars[n%16])
	}

	n -= 6 * 16
	prefixIdx := (n / 256) % 6
	if prefixIdx < 0 {
		prefixIdx = 0
	} else if prefixIdx >= len(prefixChars) {
		prefixIdx = len(prefixChars) - 1
	}
	return string(prefixChars[prefixIdx]) + string(allChars[(n/16)%16]) + string(allChars[n%16])
}

func getToolPath(toolName string) string {
	execPath, err := os.Executable()
	if err != nil {
		return toolName
	}
	dir := filepath.Dir(execPath)
	path := filepath.Join(dir, toolName)
	if _, err := os.Stat(path); err == nil {
		return path
	}
	cwd, _ := os.Getwd()
	path = filepath.Join(cwd, toolName)
	if _, err := os.Stat(path); err == nil {
		return path
	}
	return toolName
}

func runPythonCommand(args ...string) *exec.Cmd {
	return runPythonCommandContext(context.Background(), args...)
}

func runPythonCommandContext(ctx context.Context, args ...string) *exec.Cmd {
	pythonExec := "python"
	if _, err := exec.LookPath("python"); err != nil {
		pythonExec = "python3"
	}
	cmd := exec.CommandContext(ctx, pythonExec, args...)
	cmd.Env = append(os.Environ(), "TIKTOKEN_CACHE_DIR="+filepath.Join(filepath.Dir(getToolPath("token_counter.py")), "resources"))
	return cmd
}

func resolveValidationLevel(validationLevel string, strictValidation bool, validationLevelExplicit bool) (string, error) {
	if strictValidation {
		if validationLevelExplicit && validationLevel != "strict" {
			return "", fmt.Errorf("--strict-validation cannot be combined with --validation-level %s", validationLevel)
		}
		return "strict", nil
	}
	validValidationLevels := map[string]bool{"balanced": true, "strict": true}
	if !validValidationLevels[validationLevel] {
		return "", fmt.Errorf("nivel de validacao invalido: %s", validationLevel)
	}
	return validationLevel, nil
}

func estimarTokens(texto string) (int, error) {
	return estimarTokensContext(context.Background(), texto)
}

func estimarTokensContext(ctx context.Context, texto string) (int, error) {
	if texto == "" {
		return 0, nil
	}
	cmd := runPythonCommandContext(ctx, getToolPath("token_counter.py"))
	cmd.Stdin = strings.NewReader(texto)
	var stderr strings.Builder
	cmd.Stderr = &stderr
	out, err := cmd.Output()
	if err != nil {
		var exitCode int = 2
		if exitErr, ok := err.(*exec.ExitError); ok {
			exitCode = exitErr.ExitCode()
			if exitCode == 0 {
				exitCode = 2
			}
		}
		return 0, fmt.Errorf("tokenizer failed with exit code %d: %s", exitCode, strings.TrimSpace(stderr.String()))
	}
	outStr := strings.TrimSpace(string(out))
	if outStr == "" {
		return 0, fmt.Errorf("tokenizer returned empty output")
	}
	tokens, err := strconv.Atoi(outStr)
	if err != nil {
		return 0, fmt.Errorf("tokenizer output is not numeric: %q", outStr)
	}
	if tokens < 0 {
		return 0, fmt.Errorf("tokenizer output is negative: %d", tokens)
	}
	return tokens, nil
}

func calcularTER(tokens int, caracteres int) float64 {
	if caracteres > 0 {
		return float64(tokens) / float64(caracteres)
	}
	return 0
}

func ehArquivoDeTeste(root string, file string, pastaOrig string) bool {
	relPath, err := filepath.Rel(pastaOrig, root)
	if err != nil {
		return false
	}
	parts := strings.Split(strings.ToLower(relPath), string(os.PathSeparator))
	fileName := strings.ToLower(file)

	for _, p := range parts {
		if p == "test" || p == "tests" || p == "teste" || p == "testes" {
			return true
		}
	}
	return strings.Contains(fileName, "test") || strings.Contains(fileName, "teste")
}

func minificarCodigoParaIA(codigoFonte string, dicionario map[string]string) string {
	codigo := codigoFonte

	// Remove comments
	codigo = regexp.MustCompile(`(?s)/\*.*?\*/`).ReplaceAllString(codigo, "")
	codigo = regexp.MustCompile(`//.*`).ReplaceAllString(codigo, "")

	// Remove package/imports
	codigo = regexp.MustCompile(`package\s+[\w\.]+;`).ReplaceAllString(codigo, "")
	codigo = regexp.MustCompile(`import\s+(?:static\s+)?[\w\.\*]+;`).ReplaceAllString(codigo, "")

	// Remove annotations and common noise
	codigo = regexp.MustCompile(`@\w+(?:\([^)]*\))?\s*`).ReplaceAllString(codigo, "")
	codigo = regexp.MustCompile(`\b(System\.out\.\w+|console\.\w+|logger\.\w+|print|Log\.\w+|trace|debug|error|assert)\s*\([^)]*\);?`).ReplaceAllString(codigo, "")
	reStrings := regexp.MustCompile(`"(?:[^"\\]|\\.)*"|'(?:[^'\\]|\\.)*'`)
	codigo = reStrings.ReplaceAllStringFunc(codigo, func(s string) string {
		if len(s) > 15 {
			return `""`
		}
		return s
	})
	codigo = regexp.MustCompile(`\b(public|private|protected|export|final|volatile|strictfp)\s+`).ReplaceAllString(codigo, "")
	codigo = regexp.MustCompile(`\b(this|self)\.`).ReplaceAllString(codigo, "")

	// Compact
	codigo = regexp.MustCompile(`\s+`).ReplaceAllString(codigo, " ")
	codigo = regexp.MustCompile(`\s*([+\-*/%&|<>!^~?:;,{}()\[\]=]+)\s*`).ReplaceAllString(codigo, "$1")

	type kv struct {
		Key   string
		Value string
	}
	var ss []kv
	for k, v := range dicionario {
		ss = append(ss, kv{k, v})
	}
	sort.Slice(ss, func(i, j int) bool {
		return len(ss[i].Key) > len(ss[j].Key)
	})

	for _, kv := range ss {
		rePalavra := regexp.MustCompile(`\b` + regexp.QuoteMeta(kv.Key) + `\b`)
		codigo = rePalavra.ReplaceAllString(codigo, kv.Value)
	}

	return strings.TrimSpace(codigo)
}

func isBinaryFileGo(filePath string) bool {
	ext := strings.ToLower(filepath.Ext(filePath))
	binaryExtensions := map[string]bool{
		".png": true, ".jpg": true, ".jpeg": true, ".gif": true,
		".zip": true, ".pdf": true, ".exe": true, ".dll": true,
		".class": true, ".jar": true, ".db": true, ".pyc": true,
	}
	if binaryExtensions[ext] {
		return true
	}
	file, err := os.Open(filePath)
	if err != nil {
		return false
	}
	defer file.Close()
	buffer := make([]byte, 512)
	n, err := file.Read(buffer)
	if err != nil {
		return false
	}
	for i := 0; i < n; i++ {
		if buffer[i] == 0 {
			return true
		}
	}
	return false
}

func main() {
	if len(os.Args) < 2 {
		fmt.Println("Uso: motor_v3 <pasta_original> [pasta_destino] [flags]")
		os.Exit(1)
	}

	// Default choices
	pastaOrig := ""
	pastaComp := ""
	isWatcher := false
	profile := "auto"
	dictScope := "file"
	mode := "lossless"
	validationLevel := "balanced"
	failOnInflation := false
	reportFormat := "both"
	reportPath := ""
	verifySemantics := true
	dryRun := false
	continueOnError := false
	noCache := false
	durableWrites := false
	strictValidation := false
	validationLevelExplicit := false
	modeExplicit := false
	resourceProfile := "default"
	resourceProfileExplicit := false
	requestedWorkers := 0
	workersExplicit := false
	requestedMaxPythonProcesses := 0
	maxPythonProcessesExplicit := false

	// Parse flags manually to preserve existing go run motor_v3.go <src> [dst] syntax
	args := os.Args[1:]
	var positional []string

	for i := 0; i < len(args); i++ {
		arg := args[i]
		if arg == "--watcher" || arg == "-watch" {
			isWatcher = true
		} else if arg == "--fail-on-inflation" {
			failOnInflation = true
		} else if arg == "--verify-semantics" {
			verifySemantics = true
		} else if arg == "--no-verify-semantics" {
			verifySemantics = false
		} else if arg == "--dry-run" {
			dryRun = true
		} else if arg == "--continue-on-error" {
			continueOnError = true
		} else if arg == "--no-cache" {
			noCache = true
		} else if arg == "--durable-writes" {
			durableWrites = true
		} else if arg == "--strict-validation" {
			strictValidation = true
		} else if strings.HasPrefix(arg, "--validation-level=") {
			validationLevel = strings.TrimPrefix(arg, "--validation-level=")
			validationLevelExplicit = true
		} else if arg == "--validation-level" && i+1 < len(args) {
			validationLevel = args[i+1]
			validationLevelExplicit = true
			i++
		} else if strings.HasPrefix(arg, "--resource-profile=") {
			resourceProfile = strings.TrimPrefix(arg, "--resource-profile=")
			resourceProfileExplicit = true
		} else if arg == "--resource-profile" && i+1 < len(args) {
			resourceProfile = args[i+1]
			resourceProfileExplicit = true
			i++
		} else if strings.HasPrefix(arg, "--workers=") {
			parsed, err := strconv.Atoi(strings.TrimPrefix(arg, "--workers="))
			if err != nil {
				fmt.Printf("❌ Erro: --workers deve ser inteiro: %s\n", strings.TrimPrefix(arg, "--workers="))
				os.Exit(1)
			}
			requestedWorkers = parsed
			workersExplicit = true
		} else if arg == "--workers" && i+1 < len(args) {
			parsed, err := strconv.Atoi(args[i+1])
			if err != nil {
				fmt.Printf("❌ Erro: --workers deve ser inteiro: %s\n", args[i+1])
				os.Exit(1)
			}
			requestedWorkers = parsed
			workersExplicit = true
			i++
		} else if strings.HasPrefix(arg, "--max-python-processes=") {
			parsed, err := strconv.Atoi(strings.TrimPrefix(arg, "--max-python-processes="))
			if err != nil {
				fmt.Printf("❌ Erro: --max-python-processes deve ser inteiro: %s\n", strings.TrimPrefix(arg, "--max-python-processes="))
				os.Exit(1)
			}
			requestedMaxPythonProcesses = parsed
			maxPythonProcessesExplicit = true
		} else if arg == "--max-python-processes" && i+1 < len(args) {
			parsed, err := strconv.Atoi(args[i+1])
			if err != nil {
				fmt.Printf("❌ Erro: --max-python-processes deve ser inteiro: %s\n", args[i+1])
				os.Exit(1)
			}
			requestedMaxPythonProcesses = parsed
			maxPythonProcessesExplicit = true
			i++
		} else if strings.HasPrefix(arg, "--mode=") {
			mode = strings.TrimPrefix(arg, "--mode=")
			modeExplicit = true
		} else if arg == "--mode" && i+1 < len(args) {
			mode = args[i+1]
			modeExplicit = true
			i++
		} else if strings.HasPrefix(arg, "--profile=") {
			profile = strings.TrimPrefix(arg, "--profile=")
		} else if arg == "--profile" && i+1 < len(args) {
			profile = args[i+1]
			i++
		} else if strings.HasPrefix(arg, "--dictionary-scope=") {
			dictScope = strings.TrimPrefix(arg, "--dictionary-scope=")
		} else if arg == "--dictionary-scope" && i+1 < len(args) {
			dictScope = args[i+1]
			i++
		} else if strings.HasPrefix(arg, "--report=") {
			reportFormat = strings.TrimPrefix(arg, "--report=")
		} else if arg == "--report" && i+1 < len(args) {
			reportFormat = args[i+1]
			i++
		} else if strings.HasPrefix(arg, "--report-path=") {
			reportPath = strings.TrimPrefix(arg, "--report-path=")
		} else if arg == "--report-path" && i+1 < len(args) {
			reportPath = args[i+1]
			i++
		} else if strings.HasPrefix(arg, "-") {
			fmt.Printf("❌ Erro: Flag desconhecida: %s\n", arg)
			os.Exit(1)
		} else {
			positional = append(positional, arg)
		}
	}

	resolvedValidationLevel, validationErr := resolveValidationLevel(validationLevel, strictValidation, validationLevelExplicit)
	if validationErr != nil {
		fmt.Printf("❌ Erro: %s\n", validationErr)
		os.Exit(1)
	}
	validationLevel = resolvedValidationLevel

	validModes := map[string]bool{"lossless": true, "semantic": true}
	if !validModes[mode] {
		fmt.Printf("❌ Erro: Modo inválido: %s\n", mode)
		os.Exit(1)
	}

	validProfiles := map[string]bool{"auto": true, "code": true, "java": true, "markdown": true, "bmad": true}
	if !validProfiles[profile] {
		fmt.Printf("❌ Erro: Perfil inválido: %s\n", profile)
		os.Exit(1)
	}

	validDictScopes := map[string]bool{"none": true, "file": true, "corpus": true}
	if !validDictScopes[dictScope] {
		fmt.Printf("❌ Erro: Escopo do dicionário inválido: %s\n", dictScope)
		os.Exit(1)
	}

	validReports := map[string]bool{"text": true, "json": true, "both": true}
	if !validReports[reportFormat] {
		fmt.Printf("❌ Erro: Formato de relatório inválido: %s\n", reportFormat)
		os.Exit(1)
	}

	resources, resourceErr := detectResourceSettings(resourceProfile, resourceProfileExplicit, requestedWorkers, workersExplicit, requestedMaxPythonProcesses, maxPythonProcessesExplicit)
	if resourceErr != nil {
		fmt.Printf("Error: %s\n", resourceErr)
		os.Exit(1)
	}

	if len(positional) < 1 {
		fmt.Println("Uso: motor_v3 <pasta_original> [pasta_destino] [flags]")
		os.Exit(1)
	}

	pastaOrig = positional[0]
	if len(positional) > 1 {
		pastaComp = positional[1]
	}

	// Verify pastaOrig existence
	infoOrig, err := os.Stat(pastaOrig)
	if os.IsNotExist(err) {
		fmt.Printf("❌ Erro: Diretório ou arquivo de origem não encontrado: %s\n", pastaOrig)
		os.Exit(4)
	}

	// Early pre-scan for profile resolution & lossless safety validation BEFORE ANY OUTPUT CREATION
	effectiveProfile := profile
	if profile == "auto" {
		containsJavaOrCode := false
		if !infoOrig.IsDir() {
			ext := strings.ToLower(filepath.Ext(pastaOrig))
			if ext == ".java" || isCodeExtension(ext) {
				containsJavaOrCode = true
			}
		} else {
			absOrig, absErr := filepath.Abs(pastaOrig)
			if absErr != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao resolver caminho para pré-scan: %v\n", absErr)
				os.Exit(4)
			}
			if walkErr := filepath.WalkDir(absOrig, func(path string, d os.DirEntry, err error) error {
				if err != nil {
					return fmt.Errorf("failed to traverse %s during pre-scan: %w", path, err)
				}
				if d.IsDir() {
					return nil
				}
				if isBinaryFileGo(path) {
					return nil
				}
				ext := strings.ToLower(filepath.Ext(path))
				if ext == ".java" || isCodeExtension(ext) {
					containsJavaOrCode = true
					return filepath.SkipAll
				}
				return nil
			}); walkErr != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro durante pré-scan de diretório: %v\n", walkErr)
				os.Exit(4)
			}
		}
		if containsJavaOrCode {
			effectiveProfile = "code"
		}
	}

	if mode == "lossless" {
		if effectiveProfile == "code" || effectiveProfile == "java" || profile == "code" || profile == "java" {
			fmt.Println("❌ Lossless mode currently supports only Markdown and BMAD profiles. Use --mode semantic for code or Java inputs.")
			os.Exit(1)
		}
		if dictScope == "corpus" {
			fmt.Println("❌ Corpus dictionary is not currently supported in lossless mode. Use --dictionary-scope file or --mode semantic.")
			os.Exit(1)
		}
	}

	if pastaComp == "" {
		execPath, _ := os.Executable()
		execDir := filepath.Dir(execPath)
		pastaComp = filepath.Join(execDir, filepath.Base(pastaOrig)+"_mimificado")
	}

	if reportPath == "" {
		reportPath = filepath.Join(pastaComp, "report")
	}

	fmt.Println("🚀 Iniciando Otimização e Minificação (CIDA Motor Go/Python)")
	fmt.Printf("📂 Origem: %s\n📂 Destino: %s\n", pastaOrig, pastaComp)

	fmt.Printf("Logical CPUs: %d\n", resources.LogicalCPUs)
	fmt.Printf("GOMAXPROCS: %d\n", resources.GOMAXPROCS)
	fmt.Printf("Effective CPU capacity: %d\n", resources.EffectiveCPUCapacity)
	fmt.Printf("Resource profile: %s\n", resources.Profile)
	if resources.RequestedWorkers == nil {
		fmt.Println("Requested workers: <none>")
	} else {
		fmt.Printf("Requested workers: %d\n", *resources.RequestedWorkers)
	}
	fmt.Printf("Effective workers: %d\n", resources.EffectiveWorkers)
	fmt.Printf("Max Python processes: %d\n", resources.MaxPythonProcesses)
	fmt.Printf("Resolution source: %s\n", resources.ResolutionSource)
	if resources.ClampReason != "" {
		fmt.Printf("Clamp reason: %s\n", resources.ClampReason)
	}

	if isWatcher {
		fmt.Println("👀 Modo Watcher ativado. Pressione Ctrl+C para sair.")
		var lastModTimes = make(map[string]time.Time)
		for {
			changed := false
			absOrigW, absOrigErr := filepath.Abs(pastaOrig)
			absCompW, absCompErr := filepath.Abs(pastaComp)
			if absOrigErr != nil || absCompErr != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao resolver caminhos no watcher: orig=%v dest=%v\n", absOrigErr, absCompErr)
				os.Exit(4)
			}
			if walkErr := filepath.WalkDir(absOrigW, func(path string, d os.DirEntry, err error) error {
				if err != nil {
					return fmt.Errorf("failed to traverse %s in watcher: %w", path, err)
				}
				absPath, absErr := filepath.Abs(path)
				if absErr != nil {
					return fmt.Errorf("failed to resolve path %s in watcher: %w", path, absErr)
				}
				if d.IsDir() && strings.HasPrefix(absPath, absCompW) {
					return filepath.SkipDir
				}
				if !d.IsDir() {
					info, infoErr := d.Info()
					if infoErr != nil {
						return fmt.Errorf("failed to stat %s in watcher: %w", path, infoErr)
					}
					if t, ok := lastModTimes[path]; !ok || info.ModTime().After(t) {
						lastModTimes[path] = info.ModTime()
						changed = true
					}
				}
				return nil
			}); walkErr != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro durante varredura no watcher: %v\n", walkErr)
				os.Exit(4)
			}

			if changed {
				fmt.Println("🔄 Alteração detectada, recompilando...")
				processarEComparar(pastaOrig, pastaComp, mode, modeExplicit, profile, dictScope, validationLevel, validationLevelExplicit, failOnInflation, reportFormat, reportPath, verifySemantics, dryRun, continueOnError, noCache, durableWrites, resources)
			}
			time.Sleep(2 * time.Second)
		}
	} else {
		processarEComparar(pastaOrig, pastaComp, mode, modeExplicit, profile, dictScope, validationLevel, validationLevelExplicit, failOnInflation, reportFormat, reportPath, verifySemantics, dryRun, continueOnError, noCache, durableWrites, resources)
	}

}

func isCodeExtension(ext string) bool {
	codeExts := map[string]bool{
		".go": true, ".py": true, ".js": true, ".ts": true, ".jsx": true, ".tsx": true,
		".c": true, ".cpp": true, ".h": true, ".hpp": true, ".cs": true, ".java": true,
		".kt": true, ".rs": true, ".rb": true, ".php": true, ".sh": true, ".ps1": true,
		".json": true, ".yaml": true, ".yml": true, ".xml": true, ".html": true, ".css": true,
	}
	return codeExts[ext]
}

func writeAtomic(targetPath string, data []byte, durable bool) error {
	dir := filepath.Dir(targetPath)
	if err := os.MkdirAll(dir, 0755); err != nil {
		return err
	}
	tmpFile, err := os.CreateTemp(dir, ".tmp-*")
	if err != nil {
		return err
	}
	tmpName := tmpFile.Name()
	cleanup := true
	defer func() {
		if cleanup {
			tmpFile.Close()
			os.Remove(tmpName)
		}
	}()

	if _, err := tmpFile.Write(data); err != nil {
		return err
	}
	if durable {
		if err := tmpFile.Sync(); err != nil {
			return err
		}
	}
	if err := tmpFile.Close(); err != nil {
		return err
	}
	cleanup = false
	if err := os.Rename(tmpName, targetPath); err != nil {
		cleanup = true
		return err
	}
	if durable {
		// Sync the parent directory so the rename is durable (POSIX best-practice).
		// On Windows, os.File.Sync on a directory is a no-op; attempt anyway.
		if d, err := os.Open(dir); err == nil {
			_ = d.Sync() // best-effort
			d.Close()
		}
	}
	return nil
}

func writeAuxiliaryFile(targetPath string, data []byte, perm os.FileMode, durable bool) error {
	if durable {
		return writeAtomic(targetPath, data, durable)
	}
	if err := os.MkdirAll(filepath.Dir(targetPath), 0755); err != nil {
		return err
	}
	return os.WriteFile(targetPath, data, perm)
}

type JavaRawMetric struct {
	Filepath         string `json:"filepath"`
	OriginalContent  string `json:"original_content"`
	MinifiedContent  string `json:"minified_content"`
	ElapsedNs        int64  `json:"elapsed_ns"`
	DictIncluded     bool   `json:"dict_included"`
	TokensDict       int    `json:"tokens_dict"`
	TokensSidecar    int    `json:"tokens_sidecar"`
	TokensAuxiliares int    `json:"tokens_auxiliares"`
}

func processarEComparar(pastaOrig string, pastaComp string, mode string, modeExplicit bool, profile string, dictScope string, validationLevel string, validationLevelExplicit bool, failOnInflation bool, reportFormat string, reportPath string, verifySemantics bool, dryRun bool, continueOnError bool, noCache bool, durableWrites bool, resources ResourceSettings) {
	absOrig, err := filepath.Abs(pastaOrig)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Erro ao resolver caminho de origem: %v\n", err)
		os.Exit(4)
	}
	absComp, err := filepath.Abs(pastaComp)
	if err != nil {
		fmt.Fprintf(os.Stderr, "❌ Erro ao resolver caminho de destino: %v\n", err)
		os.Exit(4)
	}

	if absOrig == absComp {
		fmt.Printf("❌ Erro: Destino não pode ser igual à origem: %s\n", absOrig)
		os.Exit(4)
	}
	rel, err := filepath.Rel(absOrig, absComp)
	if err == nil && !strings.HasPrefix(rel, "..") && rel != "." {
		fmt.Printf("❌ Erro: Destino não pode ser dentro da origem: %s\n", absComp)
		os.Exit(4)
	}

	dirIsEmpty := true
	if entries, err := os.ReadDir(absComp); err == nil && len(entries) > 0 {
		dirIsEmpty = false
	}
	if _, err := os.Stat(absComp); (os.IsNotExist(err) || dirIsEmpty) && !dryRun {
		fmt.Printf("📂 Criando pasta de destino: %s\n", absComp)
		if err := os.MkdirAll(absComp, 0755); err != nil {
			fmt.Fprintf(os.Stderr, "❌ Erro ao criar diretório de destino: %v\n", err)
			os.Exit(4)
		}
		criarReadmeMinificado(absComp, absOrig, durableWrites)
	}

	// 1. Scan files
	var javaFiles []string
	var mdFiles []string
	var binaryFiles []string

	if err := filepath.WalkDir(absOrig, func(path string, d os.DirEntry, err error) error {
		if err != nil {
			return fmt.Errorf("failed to traverse %s: %w", path, err)
		}
		absPath, err := filepath.Abs(path)
		if err != nil {
			return fmt.Errorf("failed to resolve absolute path for %s: %w", path, err)
		}

		// Avoid going into destination folder
		if d.IsDir() && strings.HasPrefix(absPath, absComp) {
			return filepath.SkipDir
		}

		if !d.IsDir() {
			if strings.Contains(absPath, "_mimificado") || strings.Contains(absPath, "tknd") {
				return nil
			}

			if isBinaryFileGo(absPath) {
				binaryFiles = append(binaryFiles, absPath)
			} else if strings.HasSuffix(strings.ToLower(absPath), ".java") {
				javaFiles = append(javaFiles, absPath)
			} else if strings.HasSuffix(strings.ToLower(absPath), ".md") || strings.HasSuffix(strings.ToLower(absPath), ".txt") {
				mdFiles = append(mdFiles, absPath)
			} else {
				// Unsupported text file - preserve/copy it directly and log
				fmt.Printf("⚠️ File format not supported for optimization: preserving original %s\n", absPath)
				binaryFiles = append(binaryFiles, absPath)
			}
		}
		return nil
	}); err != nil {
		fmt.Fprintf(os.Stderr, "❌ Erro durante varredura de arquivos: %v\n", err)
		os.Exit(4)
	}

	sort.Strings(javaFiles)
	sort.Strings(mdFiles)
	sort.Strings(binaryFiles)

	// 2. Process Java files natively in Go
	var javaMetrics []JavaRawMetric
	var tempJavaJsonPath string

	if len(javaFiles) > 0 && (profile == "auto" || profile == "java" || profile == "code") {
		fmt.Println("⏳ Otimizando arquivos Java...")

		corpusHash, err := buildCorpusManifestHash(absOrig, javaFiles)
		if err != nil {
			fmt.Printf("❌ Erro ao gerar manifesto do corpus Java: %v\n", err)
			os.Exit(6)
		}

		dicionario, sidecars := construirDicionario(absOrig, javaFiles, corpusHash)

		type fileInfo struct {
			relPath         string
			destPath        string
			originalContent string
			minifiedContent string
			origTokens      int
			miniTokens      int
			elapsedNs       int64
		}

		var infos []fileInfo
		var origTokensTotal int = 0
		var miniTokensTotal int = 0
		javaJobs := make([]Job[string], 0, len(javaFiles))
		for idx, fp := range javaFiles {
			javaJobs = append(javaJobs, Job[string]{Index: idx, Value: fp})
		}
		poolOutcome := RunPoolOutcomeWithOptions(context.Background(), resources.EffectiveWorkers, javaJobs, func(ctx context.Context, fp string) (fileInfo, error) {
			relPath, err := filepath.Rel(absOrig, fp)
			if err != nil {
				return fileInfo{}, &SourceIOProcessingError{Path: fp, Err: fmt.Errorf("erro ao calcular caminho relativo: %w", err)}
			}
			destPath := filepath.Join(absComp, relPath) + ".tknc"

			contentBytes, err := os.ReadFile(fp)
			if err != nil {
				return fileInfo{}, &SourceIOProcessingError{Path: fp, Err: fmt.Errorf("erro ao ler arquivo Java: %w", err)}
			}
			contentStr := string(contentBytes)
			origTok, err := estimarTokensContext(ctx, contentStr)
			if err != nil {
				return fileInfo{}, &TokenizerProcessingError{Path: fp, Err: fmt.Errorf("erro no tokenizer ao processar original: %w", err)}
			}

			start := time.Now()
			minified := minificarCodigoParaIA(contentStr, dicionario)
			elapsed := time.Since(start).Nanoseconds()

			miniTok, err := estimarTokensContext(ctx, minified)
			if err != nil {
				return fileInfo{}, &TokenizerProcessingError{Path: fp, Err: fmt.Errorf("erro no tokenizer ao processar minificado: %w", err)}
			}

			return fileInfo{
				relPath:         relPath,
				destPath:        destPath,
				originalContent: contentStr,
				minifiedContent: minified,
				origTokens:      origTok,
				miniTokens:      miniTok,
				elapsedNs:       elapsed,
			}, nil
		}, PoolOptions{ContinueOnError: continueOnError})

		for _, result := range poolOutcome.Results {
			if result.Err != nil {
				fmt.Fprintf(os.Stderr, "Error processing Java file: %v\n", result.Err)
				if !continueOnError {
					os.Exit(exitCodeForJavaProcessingError(poolOutcome.RootError))
				}
				continue
			}
			infos = append(infos, result.Value)
			origTokensTotal += result.Value.origTokens
			miniTokensTotal += result.Value.miniTokens
		}
		sequentialJavaFiles := []string{}

		for _, fp := range sequentialJavaFiles {
			relPath, err := filepath.Rel(absOrig, fp)
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao calcular caminho relativo para %s: %v\n", fp, err)
				os.Exit(4)
			}
			destPath := filepath.Join(absComp, relPath) + ".tknc"

			contentBytes, err := os.ReadFile(fp)
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao ler arquivo Java %s: %v\n", fp, err)
				os.Exit(4)
			}
			contentStr := string(contentBytes)
			origTok, err := estimarTokens(contentStr)
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro no tokenizer ao processar original %s: %v\n", fp, err)
				os.Exit(2)
			}

			start := time.Now()
			minified := minificarCodigoParaIA(contentStr, dicionario)
			elapsed := time.Since(start).Nanoseconds()

			miniTok, err := estimarTokens(minified)
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro no tokenizer ao processar minificado %s: %v\n", fp, err)
				os.Exit(2)
			}

			infos = append(infos, fileInfo{
				relPath:         relPath,
				destPath:        destPath,
				originalContent: contentStr,
				minifiedContent: minified,
				origTokens:      origTok,
				miniTokens:      miniTok,
				elapsedNs:       elapsed,
			})
			origTokensTotal += origTok
			miniTokensTotal += miniTok
		}

		var sidecarTokensTotal int = 0
		for _, sidecar := range sidecars {
			sidecarBytes, err := json.MarshalIndent(sidecar, "", "    ")
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao serializar sidecar JSON: %v\n", err)
				os.Exit(6)
			}
			toks, err := estimarTokens(string(sidecarBytes))
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro no tokenizer ao processar sidecar: %v\n", err)
				os.Exit(2)
			}
			sidecarTokensTotal += toks
		}

		translateTokens, err := estimarTokens(getTranslatePyContent())
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Erro no tokenizer ao processar tradutor: %v\n", err)
			os.Exit(2)
		}

		totalOverhead := sidecarTokensTotal + translateTokens
		grossSavings := origTokensTotal - miniTokensTotal
		netSavings := grossSavings - totalOverhead

		var useDictionary bool = false
		if netSavings > 0 {
			useDictionary = true
			fmt.Printf("✓ Java corpus optimization has net token savings: %d tokens. Applying dictionary minification.\n", netSavings)
		} else {
			useDictionary = false
			fmt.Printf("⚠️ Java corpus optimization yields no net gain (net savings: %d tokens). Reverting to original source.\n", netSavings)
		}

		var distributedSidecarSum int = 0
		var distributedAuxSum int = 0
		for idx, info := range infos {
			var dictIncluded bool = false
			var tokensSidecar int = 0
			var tokensAux int = 0
			var finalContent string

			if useDictionary {
				dictIncluded = true
				if origTokensTotal > 0 {
					if idx == len(infos)-1 {
						tokensSidecar = sidecarTokensTotal - distributedSidecarSum
						tokensAux = translateTokens - distributedAuxSum
					} else {
						tokensSidecar = int(float64(sidecarTokensTotal) * float64(info.origTokens) / float64(origTokensTotal))
						tokensAux = int(float64(translateTokens) * float64(info.origTokens) / float64(origTokensTotal))
						distributedSidecarSum += tokensSidecar
						distributedAuxSum += tokensAux
					}
				}
				finalContent = info.minifiedContent
			} else {
				dictIncluded = false
				tokensSidecar = 0
				tokensAux = 0
				finalContent = info.originalContent
			}

			if !dryRun {
				contentToSave := strings.ReplaceAll(finalContent, "\r\n", "\n")
				if err := writeAtomic(info.destPath, []byte(contentToSave), durableWrites); err != nil {
					fmt.Fprintf(os.Stderr, "❌ Erro ao escrever %s: %v\n", info.destPath, err)
					os.Exit(4)
				}
			}

			javaMetrics = append(javaMetrics, JavaRawMetric{
				Filepath:         info.relPath,
				OriginalContent:  info.originalContent,
				MinifiedContent:  finalContent,
				ElapsedNs:        info.elapsedNs,
				DictIncluded:     dictIncluded,
				TokensDict:       tokensSidecar + tokensAux,
				TokensSidecar:    tokensSidecar,
				TokensAuxiliares: tokensAux,
			})
		}

		if useDictionary && !dryRun {
			tkndDir := filepath.Join(absComp, "tknd")
			criarReadmeTknd(tkndDir, durableWrites)
			for startID, sidecar := range sidecars {
				fileName := fmt.Sprintf("%s.cidatkn", startID)
				fileBytes, err := json.MarshalIndent(sidecar, "", "    ")
				if err != nil {
					fmt.Fprintf(os.Stderr, "❌ Erro ao serializar sidecar JSON: %v\n", err)
					os.Exit(6)
				}
				if err := writeAtomic(filepath.Join(tkndDir, fileName), fileBytes, durableWrites); err != nil {
					fmt.Fprintf(os.Stderr, "❌ Erro ao escrever sidecar: %v\n", err)
					os.Exit(4)
				}
			}
			gerarScriptTraducao(absComp, durableWrites)
		}

		if len(javaMetrics) > 0 {
			javaMetricsJson, err := json.Marshal(javaMetrics)
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao serializar métricas Java: %v\n", err)
				os.Exit(6)
			}
			if dryRun {
				tempDir, err := os.MkdirTemp("", "cida_dryrun_*")
				if err == nil {
					tempJavaJsonPath = filepath.Join(tempDir, ".cida_java_raw.json")
					if err := writeAtomic(tempJavaJsonPath, javaMetricsJson, durableWrites); err != nil {
						fmt.Fprintf(os.Stderr, "❌ Erro ao escrever JSON temporário Java (dry-run): %v\n", err)
						os.Exit(4)
					}
					defer os.RemoveAll(tempDir)
				}
			} else {
				tempJavaJsonPath = filepath.Join(absComp, ".cida_java_raw.json")
				if err := writeAtomic(tempJavaJsonPath, javaMetricsJson, durableWrites); err != nil {
					fmt.Fprintf(os.Stderr, "❌ Erro ao escrever JSON temporário Java: %v\n", err)
					os.Exit(4)
				}
			}
		}
	}

	// 3. Process Markdown files in batch using Python and compile report
	if len(mdFiles) > 0 || len(javaFiles) > 0 || profile == "markdown" || profile == "bmad" || profile == "java" {
		fmt.Println("⏳ Otimizando arquivos Markdown/BMAD via Python Core...")

		pyArgs := []string{
			getToolPath("token_optimizer.py"),
			"--src", absOrig,
			"--dst", absComp,
			"--profile", profile,
			"--dictionary-scope", dictScope,
			"--report", reportFormat,
			"--report-path", reportPath,
		}
		if modeExplicit || mode != "lossless" {
			pyArgs = append(pyArgs, "--mode", mode)
		}
		if validationLevelExplicit || validationLevel != "balanced" {
			pyArgs = append(pyArgs, "--validation-level", validationLevel)
		}
		if failOnInflation {
			pyArgs = append(pyArgs, "--fail-on-inflation")
		}
		if verifySemantics {
			pyArgs = append(pyArgs, "--verify-semantics")
		} else {
			pyArgs = append(pyArgs, "--no-verify-semantics")
		}
		if dryRun {
			pyArgs = append(pyArgs, "--dry-run")
		}
		if continueOnError {
			pyArgs = append(pyArgs, "--continue-on-error")
		}
		if noCache {
			pyArgs = append(pyArgs, "--no-cache")
		}
		if durableWrites {
			pyArgs = append(pyArgs, "--durable-writes")
		}
		pyArgs = append(pyArgs, "--resource-profile", resources.Profile)
		pyArgs = append(pyArgs, "--workers", strconv.Itoa(resources.EffectiveWorkers))
		pyArgs = append(pyArgs, "--max-python-processes", strconv.Itoa(resources.MaxPythonProcesses))
		pyArgs = append(pyArgs, "--logical-cpus", strconv.Itoa(resources.LogicalCPUs))
		pyArgs = append(pyArgs, "--gomaxprocs", strconv.Itoa(resources.GOMAXPROCS))
		pyArgs = append(pyArgs, "--effective-cpu-capacity", strconv.Itoa(resources.EffectiveCPUCapacity))
		pyArgs = append(pyArgs, "--resource-resolution-source", resources.ResolutionSource)
		if resources.RequestedWorkers != nil {
			pyArgs = append(pyArgs, "--requested-workers", strconv.Itoa(*resources.RequestedWorkers))
		}
		// If Java files were processed, pass the raw json parameter
		if _, err := os.Stat(filepath.Join(absComp, ".cida_java_raw.json")); err == nil {
			pyArgs = append(pyArgs, "--java-raw-json", filepath.Join(absComp, ".cida_java_raw.json"))
		}

		cmd := runPythonCommand(pyArgs...)
		cmd.Stdout = os.Stdout
		cmd.Stderr = os.Stderr
		if err := cmd.Run(); err != nil {
			if exitError, ok := err.(*exec.ExitError); ok {
				exitCode := exitError.ExitCode()
				fmt.Printf("⚠️ Otimizador Python falhou com código: %d\n", exitCode)
				os.Exit(exitCode)
			} else {
				fmt.Printf("⚠️ Erro ao executar o otimizador Python: %v\n", err)
				os.Exit(6) // Subprocess error
			}
		}
	}

	// 4. Copy binary files
	if len(binaryFiles) > 0 && !dryRun {
		fmt.Printf("⏳ Copiando %d arquivos binários...\n", len(binaryFiles))
		for _, bf := range binaryFiles {
			relPath, _ := filepath.Rel(absOrig, bf)
			destPath := filepath.Join(absComp, relPath)
			input, err := os.ReadFile(bf)
			if err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao ler arquivo binário %s: %v\n", bf, err)
				os.Exit(4)
			}
			if err := writeAtomic(destPath, input, durableWrites); err != nil {
				fmt.Fprintf(os.Stderr, "❌ Erro ao escrever arquivo binário %s: %v\n", destPath, err)
				os.Exit(4)
			}
		}
	}
}

func criarReadmeMinificado(pastaDestino string, pastaOrigem string, durable bool) {
	conteudo := `# ⚠️ PROJETO MINIFICADO - SOMENTE LEITURA

Este diretório contém uma versão otimizada (minificada) do seu código, gerada automaticamente para reduzir drasticamente o consumo de tokens em modelos de linguagem (LLMs).

## 🚨 AVISO IMPORTANTE: Fluxo de Trabalho

1. LEIA APENAS ESTA PASTA (Minificada): Use os arquivos nesta pasta como contexto. Eles foram processados pelo motor de minificação e contêm o comportamento puro do sistema.

2. EDITE APENAS A PASTA ORIGINAL (Não Minificada): Todas as alterações, refatorações, correções de bugs e novas funcionalidades devem ser feitas exclusivamente na pasta fonte original:
   <ORIGINAL_SOURCE_ROOT>

3. NUNCA EDITE ARQUIVOS NESTA PASTA: Esta pasta é gerenciada por um motor automático. Qualquer alteração manual aqui será sobrescrita na próxima execução da compilação.

## 🤖 Orientações para a I.A.
- Ferramenta de Tradução (translate.py): Caso seja estritamente necessário entender um identificador, utilize o script 'translate.py' na raiz do projeto original. *AVISO: Use esta ferramenta apenas quando necessário e armazene a tradução em seu contexto imediato para evitar chamadas redundantes.*
- Edição: As sugestões de código devem ser baseadas na estrutura da pasta original.
`

	for _, pair := range []struct{ name, content string }{
		{"README_MINIFICADO.md", conteudo},
		{"CONSTITUTION.md", getConstitutionContent()},
		{"AGENTS.md", getAgentsContent()},
		{"PROMPT_INICIAL.MD", getPromptInicialContent()},
	} {
		target := filepath.Join(pastaDestino, pair.name)
		if err := writeAuxiliaryFile(target, []byte(pair.content), 0644, durable); err != nil {
			fmt.Fprintf(os.Stderr, "❌ Erro ao escrever %s: %v\n", target, err)
			os.Exit(4)
		}
	}
}

func criarReadmeTknd(pastaTknd string, durable bool) {
	conteudo := `# Diretório de Dicionários de Tokens (tknd)

Este diretório contém o mapeamento completo entre os identificadores ofuscados encontrados no código minificado e os seus nomes originais.

## Estrutura dos Arquivos
Os arquivos estão segmentados em blocos de 500 registros para facilitar a consulta pela I.A. 
Cada arquivo é nomeado de acordo com o identificador do primeiro token contido nele (ex: A0.cidatkn contém os mapeamentos de A0 a A1F3...).

## Como utilizar
Sempre que encontrar um identificador ofuscado (ex: A5), procure no arquivo correspondente dentro desta pasta para identificar sua função ou nome original.

## Ferramenta de Tradução (translate.py)
Para facilitar a tradução automática, utilize o script 'translate.py' disponível na raiz do projeto original passando os tokens como argumento.
Exemplo: python3 translate.py A0 B1
`
	target := filepath.Join(pastaTknd, "README.md")
	if err := writeAuxiliaryFile(target, []byte(conteudo), 0644, durable); err != nil {
		fmt.Fprintf(os.Stderr, "❌ Erro ao escrever README do tknd: %v\n", err)
		os.Exit(4)
	}
}

func getPromptInicialContent() string {
	return `# Instruções Iniciais para a I.A.

A primeira coisa que você deve fazer ao receber este código é ler e compreender rigorosamente a **CONSTITUTION.md**, a **AGENTS.md** e o **README_MINIFICADO.md**.

## Regras de Comportamento após o Entendimento:
1. **Atuação:** Você não deve se referir a este projeto como um "projeto minificado" ou mencionar termos técnicos de ofuscação (B16, tokens) em suas respostas aos usuários.
2. **Abordagem:** Responda ao usuário como se estivesse lidando com um projeto de código fonte padrão, humano e legível. 
5. **Ferramenta de Tradução:** Caso seja estritamente necessário entender um identificador ofuscado, utilize o script 'translate.py' localizado na raiz do projeto original. *AVISO: Use esta ferramenta apenas quando necessário e armazene a tradução em seu contexto imediato para evitar chamadas redundantes.*
`
}

func getConstitutionContent() string {
	return `# Constituição do Motor de Minificação

## Regras de Funcionamento
1. Minificação Extrema: Remove ruídos, comentários, espaçamentos, tipagens desnecessárias e modificadores de acesso.
2. Mapeamento (Tokens B16): Termos críticos substituídos por IDs B16 (A0, A1... AF, B0...).
5. Ferramenta de Tradução (translate.py): Caso seja estritamente necessário entender um identificador, utilize o script 'translate.py' na raiz do projeto original passando o token como argumento. *AVISO: Armazene a tradução em seu contexto imediato para evitar chamadas redundantes.*
`
}

func getAgentsContent() string {
	return `# Agentes de Processamento

Este projeto está otimizado para agentes via Minificação Extrema.

## Regras de Consulta ao Dicionário
1. Leitura: Consulte esta pasta para entender o comportamento puro.
## Ferramenta de Tradução (translate.py)
Caso seja estritamente necessário entender um identificador, utilize o script 'translate.py' na raiz do projeto original passando o token como argumento.
*AVISO:* Use esta ferramenta apenas quando necessário e armazene a tradução em seu contexto imediato para evitar chamadas redundantes.

⚠️ NUNCA edite arquivos nesta pasta de minificação. Eles são read-only para otimização de contexto e serão sobrescritos.
`
}

type SidecarData struct {
	Format       string            `json:"format"`
	Version      int               `json:"version"`
	Source       string            `json:"source"`
	SourceSha256 string            `json:"source_sha256"`
	Entries      map[string]string `json:"entries"`
}

type ManifestFile struct {
	Path   string `json:"path"`
	Sha256 string `json:"sha256"`
}

type Manifest struct {
	Files []ManifestFile `json:"files"`
}

func fileSHA256(filePath string) (string, error) {
	data, err := os.ReadFile(filePath)
	if err != nil {
		return "", err
	}
	hash := sha256.Sum256(data)
	return fmt.Sprintf("%x", hash), nil
}

func buildCorpusManifestHash(absOrig string, javaFiles []string) (string, error) {
	var files []ManifestFile
	for _, fp := range javaFiles {
		relPath, err := filepath.Rel(absOrig, fp)
		if err != nil {
			return "", err
		}
		relPath = filepath.ToSlash(relPath)
		sha, err := fileSHA256(fp)
		if err != nil {
			return "", err
		}
		files = append(files, ManifestFile{
			Path:   relPath,
			Sha256: sha,
		})
	}

	sort.Slice(files, func(i, j int) bool {
		return files[i].Path < files[j].Path
	})

	manifest := Manifest{Files: files}
	manifestBytes, err := json.Marshal(manifest)
	if err != nil {
		return "", err
	}

	hash := sha256.Sum256(manifestBytes)
	return fmt.Sprintf("%x", hash), nil
}

func getTranslatePyContent() string {
	return `import os
import sys
import json

def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result

def translate(tokens, tknd_dir):
    mapping = {}
    if not os.path.exists(tknd_dir):
        print(f"Erro: Pasta {tknd_dir} não encontrada.", file=sys.stderr)
        sys.exit(5)
    
    for file in os.listdir(tknd_dir):
        if file.endswith(".cidatkn"):
            try:
                with open(os.path.join(tknd_dir, file), 'r', encoding='utf-8') as f:
                    data = json.load(f, object_pairs_hook=reject_duplicate_keys)
                    if isinstance(data, dict) and "entries" in data:
                        for alias, val in data["entries"].items():
                            mapping[alias] = val
            except Exception as e:
                print(f"Erro ao ler dicionário {file}: {e}", file=sys.stderr)
                sys.exit(5)
    
    results = {}
    for t in tokens:
        results[t] = mapping.get(t, "Não encontrado")
    return results

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Uso: python3 translate.py [ID1] [ID2] ... [--path <caminho_da_pasta_tknd>]")
    else:
        tknd_dir = os.path.join(os.getcwd(), "tknd")
        
        args = sys.argv[1:]
        if "--path" in args:
            idx = args.index("--path")
            if idx + 1 < len(args):
                tknd_dir = args[idx+1]
                args = args[:idx] + args[idx+2:]
        
        print(translate(args, tknd_dir))
`
}

func gerarScriptTraducao(pastaDestino string, durable bool) {
	conteudo := getTranslatePyContent()
	target := filepath.Join(pastaDestino, "translate.py")
	if err := writeAuxiliaryFile(target, []byte(conteudo), 0755, durable); err != nil {
		fmt.Fprintf(os.Stderr, "❌ Erro ao escrever script de tradução: %v\n", err)
		os.Exit(4)
	}
}

func construirDicionario(pastaOrig string, javaFiles []string, corpusHash string) (map[string]string, map[string]SidecarData) {
	contador := make(map[string]int)
	rePalavras := regexp.MustCompile(`\b[a-zA-Z_]{6,}\b`)

	for _, path := range javaFiles {
		conteudo, err := os.ReadFile(path)
		if err != nil {
			fmt.Fprintf(os.Stderr, "❌ Erro ao ler arquivo Java em construirDicionário %s: %v\n", path, err)
			os.Exit(4)
		}
		palavras := rePalavras.FindAllString(string(conteudo), -1)
		for _, p := range palavras {
			contador[p]++
		}
	}

	type kv struct {
		Key  string
		Freq int
	}
	var ss []kv
	for k, v := range contador {
		ss = append(ss, kv{k, v})
	}
	sort.Slice(ss, func(i, j int) bool {
		scoreI := ss[i].Freq * len(ss[i].Key)
		scoreJ := ss[j].Freq * len(ss[j].Key)
		if scoreI == scoreJ {
			return ss[i].Key < ss[j].Key
		}
		return scoreI > scoreJ
	})

	dicionario := make(map[string]string)
	sidecars := make(map[string]SidecarData)

	for i := 0; i < len(ss); i += 500 {
		end := i + 500
		if end > len(ss) {
			end = len(ss)
		}

		startID := getB16ID(i)

		var entries []struct{ Alias, Value string }
		for j := i; j < end; j++ {
			if ss[j].Freq >= 3 {
				token := getB16ID(j)
				dicionario[ss[j].Key] = token
				entries = append(entries, struct{ Alias, Value string }{token, ss[j].Key})
			}
		}

		if len(entries) > 0 {
			sort.Slice(entries, func(x, y int) bool {
				return entries[x].Alias < entries[y].Alias
			})

			entriesMap := make(map[string]string)
			for _, entry := range entries {
				entriesMap[entry.Alias] = entry.Value
			}

			sidecar := SidecarData{
				Format:       "cida-token-sidecar",
				Version:      1,
				Source:       "corpus",
				SourceSha256: corpusHash,
				Entries:      entriesMap,
			}

			sidecars[startID] = sidecar
		}
	}
	return dicionario, sidecars
}

const (
	defaultWorkerCount = 10
	minWorkerCount     = 1
	maxWorkerCount     = 256
)

type ResourceSettings struct {
	LogicalCPUs          int    `json:"logical_cpus"`
	GOMAXPROCS           int    `json:"gomaxprocs"`
	EffectiveCPUCapacity int    `json:"effective_cpu_capacity"`
	Profile              string `json:"profile"`
	RequestedWorkers     *int   `json:"requested_workers"`
	EffectiveWorkers     int    `json:"effective_workers"`
	MaxPythonProcesses   int    `json:"max_python_processes"`
	ResolutionSource     string `json:"resolution_source"`
	ClampReason          string `json:"clamp_reason,omitempty"`
}

func resolveResourceSettings(profile string, profileExplicit bool, requestedWorkers int, workersExplicit bool, requestedMaxPythonProcesses int, maxPythonProcessesExplicit bool, numCPU func() int, gomaxprocs func() int) (ResourceSettings, error) {
	logicalCPUs := numCPU()
	if logicalCPUs < 1 {
		logicalCPUs = 1
	}
	gmp := gomaxprocs()
	if gmp < 1 {
		gmp = 1
	}
	effectiveCPUCapacity := minInt(logicalCPUs, gmp)
	if effectiveCPUCapacity < 1 {
		effectiveCPUCapacity = 1
	}

	settings := ResourceSettings{
		LogicalCPUs:          logicalCPUs,
		GOMAXPROCS:           gmp,
		EffectiveCPUCapacity: effectiveCPUCapacity,
		Profile:              "default",
		EffectiveWorkers:     defaultWorkerCount,
		ResolutionSource:     "default",
	}
	if gmp < logicalCPUs && settings.EffectiveWorkers > effectiveCPUCapacity {
		settings.EffectiveWorkers = effectiveCPUCapacity
		settings.ClampReason = "gomaxprocs"
	}

	if profileExplicit {
		workers, err := workersForProfile(profile, effectiveCPUCapacity)
		if err != nil {
			return settings, err
		}
		settings.Profile = profile
		settings.EffectiveWorkers = workers
		settings.ResolutionSource = "profile"
	}

	if workersExplicit {
		if requestedWorkers < minWorkerCount || requestedWorkers > maxWorkerCount {
			return settings, fmt.Errorf("--workers must be between %d and %d", minWorkerCount, maxWorkerCount)
		}
		value := requestedWorkers
		settings.RequestedWorkers = &value
		settings.EffectiveWorkers = requestedWorkers
		settings.ResolutionSource = "explicit_workers"
		if !profileExplicit {
			settings.Profile = "custom"
		}
	}

	if maxPythonProcessesExplicit {
		if requestedMaxPythonProcesses < minWorkerCount || requestedMaxPythonProcesses > maxWorkerCount {
			return settings, fmt.Errorf("--max-python-processes must be between %d and %d", minWorkerCount, maxWorkerCount)
		}
		settings.MaxPythonProcesses = minInt(requestedMaxPythonProcesses, settings.EffectiveWorkers)
	} else {
		settings.MaxPythonProcesses = minInt(settings.EffectiveWorkers, minInt(4, effectiveCPUCapacity))
	}
	if settings.MaxPythonProcesses < 1 {
		settings.MaxPythonProcesses = 1
	}

	return settings, nil
}

func detectResourceSettings(profile string, profileExplicit bool, requestedWorkers int, workersExplicit bool, requestedMaxPythonProcesses int, maxPythonProcessesExplicit bool) (ResourceSettings, error) {
	return resolveResourceSettings(
		profile,
		profileExplicit,
		requestedWorkers,
		workersExplicit,
		requestedMaxPythonProcesses,
		maxPythonProcessesExplicit,
		runtime.NumCPU,
		func() int { return runtime.GOMAXPROCS(0) },
	)
}

func workersForProfile(profile string, logicalCPUs int) (int, error) {
	if logicalCPUs < 1 {
		logicalCPUs = 1
	}
	switch profile {
	case "light":
		return clampWorkers(maxInt(1, minInt(4, logicalCPUs/2))), nil
	case "medium":
		return clampWorkers(minInt(10, maxInt(1, logicalCPUs))), nil
	case "hard":
		return clampWorkers(minInt(64, maxInt(1, logicalCPUs*2))), nil
	default:
		return 0, fmt.Errorf("invalid resource profile: %s", profile)
	}
}

func clampWorkers(workers int) int {
	if workers < minWorkerCount {
		return minWorkerCount
	}
	if workers > maxWorkerCount {
		return maxWorkerCount
	}
	return workers
}

func minInt(a int, b int) int {
	if a < b {
		return a
	}
	return b
}

func maxInt(a int, b int) int {
	if a > b {
		return a
	}
	return b
}

func exitCodeForJavaProcessingError(err error) int {
	if err == nil {
		return 6
	}
	var tokenizerErr *TokenizerProcessingError
	if errors.As(err, &tokenizerErr) {
		return 2
	}
	var sourceErr *SourceIOProcessingError
	if errors.As(err, &sourceErr) {
		return 4
	}
	var panicErr *WorkerPanicError
	if errors.As(err, &panicErr) {
		return 6
	}
	return 6
}

type TokenizerProcessingError struct {
	Path string
	Err  error
}

func (err *TokenizerProcessingError) Error() string {
	return fmt.Sprintf("tokenizer error for %s: %v", err.Path, err.Err)
}

func (err *TokenizerProcessingError) Unwrap() error {
	return err.Err
}

type SourceIOProcessingError struct {
	Path string
	Err  error
}

func (err *SourceIOProcessingError) Error() string {
	return fmt.Sprintf("source I/O error for %s: %v", err.Path, err.Err)
}

func (err *SourceIOProcessingError) Unwrap() error {
	return err.Err
}

type WorkerPanicError struct {
	Value any
}

func (err *WorkerPanicError) Error() string {
	return fmt.Sprintf("worker panic: %v", err.Value)
}

type PoolConfigurationError struct {
	Message string
}

func (err *PoolConfigurationError) Error() string {
	return err.Message
}

type Job[T any] struct {
	Index int
	Value T
}

type Result[R any] struct {
	Index int
	Value R
	Err   error
}

type PoolOutcome[R any] struct {
	Results   []Result[R]
	RootError error
}

type PoolOptions struct {
	ContinueOnError bool
}

func RunPool[T any, R any](
	ctx context.Context,
	workers int,
	jobs []Job[T],
	fn func(context.Context, T) (R, error),
) []Result[R] {
	return RunPoolWithOptions(ctx, workers, jobs, fn, PoolOptions{})
}

func RunPoolWithOptions[T any, R any](
	ctx context.Context,
	workers int,
	jobs []Job[T],
	fn func(context.Context, T) (R, error),
	options PoolOptions,
) []Result[R] {
	return RunPoolOutcomeWithOptions(ctx, workers, jobs, fn, options).Results
}

func RunPoolOutcomeWithOptions[T any, R any](
	ctx context.Context,
	workers int,
	jobs []Job[T],
	fn func(context.Context, T) (R, error),
	options PoolOptions,
) PoolOutcome[R] {
	if workers < 1 {
		workers = 1
	}
	if workers > len(jobs) && len(jobs) > 0 {
		workers = len(jobs)
	}

	results := make([]Result[R], len(jobs))
	if err := validateJobs(jobs); err != nil {
		for position, job := range jobs {
			results[position] = Result[R]{Index: job.Index, Err: err}
		}
		return PoolOutcome[R]{Results: results, RootError: err}
	}
	if len(jobs) == 0 {
		return PoolOutcome[R]{Results: results}
	}

	poolCtx, cancel := context.WithCancel(ctx)
	defer cancel()

	type queuedJob struct {
		position int
		job      Job[T]
	}

	jobCh := make(chan queuedJob)
	var wg sync.WaitGroup
	var rootMu sync.Mutex
	var rootError error

	recordRootError := func(err error) {
		if err == nil {
			return
		}
		rootMu.Lock()
		defer rootMu.Unlock()
		if shouldPromoteRootError(err, rootError) {
			rootError = err
		}
	}

	for workerID := 0; workerID < workers; workerID++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for queued := range jobCh {
				job := queued.job
				position := queued.position
				if poolCtx.Err() != nil && !options.ContinueOnError {
					results[position] = Result[R]{Index: job.Index, Err: poolCtx.Err()}
					continue
				}
				value, err := runPoolJob(poolCtx, job.Value, fn)
				results[position] = Result[R]{Index: job.Index, Value: value, Err: err}
				if err != nil && !options.ContinueOnError {
					recordRootError(err)
					cancel()
				} else if err != nil {
					recordRootError(err)
				}
			}
		}()
	}

	for position, job := range jobs {
		if poolCtx.Err() != nil && !options.ContinueOnError {
			results[position] = Result[R]{Index: job.Index, Err: poolCtx.Err()}
			continue
		}
		select {
		case jobCh <- queuedJob{position: position, job: job}:
		case <-poolCtx.Done():
			if !options.ContinueOnError {
				results[position] = Result[R]{Index: job.Index, Err: poolCtx.Err()}
			}
		}
	}
	close(jobCh)
	wg.Wait()

	rootMu.Lock()
	finalRootError := rootError
	rootMu.Unlock()
	return PoolOutcome[R]{Results: results, RootError: finalRootError}
}

func runPoolJob[T any, R any](
	ctx context.Context,
	value T,
	fn func(context.Context, T) (R, error),
) (result R, err error) {
	defer func() {
		if recovered := recover(); recovered != nil {
			err = &WorkerPanicError{Value: recovered}
		}
	}()
	return fn(ctx, value)
}

func validateJobs[T any](jobs []Job[T]) error {
	seen := make(map[int]struct{}, len(jobs))
	for _, job := range jobs {
		if job.Index < 0 || job.Index >= len(jobs) {
			return &PoolConfigurationError{Message: fmt.Sprintf("invalid job index %d for %d jobs", job.Index, len(jobs))}
		}
		if _, exists := seen[job.Index]; exists {
			return &PoolConfigurationError{Message: fmt.Sprintf("duplicate job index %d", job.Index)}
		}
		seen[job.Index] = struct{}{}
	}
	return nil
}

func shouldPromoteRootError(candidate error, current error) bool {
	if candidate == nil {
		return false
	}
	if current == nil {
		return true
	}
	candidateCanceled := errors.Is(candidate, context.Canceled)
	currentCanceled := errors.Is(current, context.Canceled)
	if currentCanceled && !candidateCanceled {
		return true
	}
	if candidateCanceled && !currentCanceled {
		return false
	}
	return false
}
