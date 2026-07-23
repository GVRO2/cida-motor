package main

import (
	"crypto/sha256"
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
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
	cmd := exec.Command("python", args...)
	// Set environment variable for offline cache
	cmd.Env = append(os.Environ(), "TIKTOKEN_CACHE_DIR="+filepath.Join(filepath.Dir(getToolPath("token_counter.py")), "resources"))
	if err := cmd.Start(); err != nil {
		cmd = exec.Command("python3", args...)
		cmd.Env = append(os.Environ(), "TIKTOKEN_CACHE_DIR="+filepath.Join(filepath.Dir(getToolPath("token_counter.py")), "resources"))
	} else {
		cmd.Process.Kill()
		cmd = exec.Command("python", args...)
		cmd.Env = append(os.Environ(), "TIKTOKEN_CACHE_DIR="+filepath.Join(filepath.Dir(getToolPath("token_counter.py")), "resources"))
	}
	return cmd
}

func estimarTokens(texto string) (int, error) {
	if texto == "" {
		return 0, nil
	}
	cmd := runPythonCommand(getToolPath("token_counter.py"))
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
	failOnInflation := false
	reportFormat := "both"
	reportPath := ""
	verifySemantics := true
	dryRun := false

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
		} else if arg == "--dry-run" {
			dryRun = true
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

	if len(positional) < 1 {
		fmt.Println("Uso: motor_v3 <pasta_original> [pasta_destino] [flags]")
		os.Exit(1)
	}

	pastaOrig = positional[0]
	if len(positional) > 1 {
		pastaComp = positional[1]
	}

	// Verify pastaOrig existence
	if _, err := os.Stat(pastaOrig); os.IsNotExist(err) {
		fmt.Printf("❌ Erro: Diretório ou arquivo de origem não encontrado: %s\n", pastaOrig)
		os.Exit(4)
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

	if isWatcher {
		fmt.Println("👀 Modo Watcher ativado. Pressione Ctrl+C para sair.")
		var lastModTimes = make(map[string]time.Time)
		for {
			changed := false
			filepath.WalkDir(pastaOrig, func(path string, d os.DirEntry, err error) error {
				if err != nil { return nil }
				absPath, _ := filepath.Abs(path)
				absComp, _ := filepath.Abs(pastaComp)
				if d.IsDir() && strings.HasPrefix(absPath, absComp) {
					return filepath.SkipDir
				}
				if !d.IsDir() {
					info, _ := d.Info()
					if t, ok := lastModTimes[path]; !ok || info.ModTime().After(t) {
						lastModTimes[path] = info.ModTime()
						changed = true
					}
				}
				return nil
			})

			if changed {
				fmt.Println("🔄 Alteração detectada, recompilando...")
				processarEComparar(pastaOrig, pastaComp, profile, dictScope, failOnInflation, reportFormat, reportPath, verifySemantics, dryRun)
			}
			time.Sleep(2 * time.Second)
		}
	} else {
		processarEComparar(pastaOrig, pastaComp, profile, dictScope, failOnInflation, reportFormat, reportPath, verifySemantics, dryRun)
	}
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

func processarEComparar(pastaOrig string, pastaComp string, profile string, dictScope string, failOnInflation bool, reportFormat string, reportPath string, verifySemantics bool, dryRun bool) {
	absOrig, _ := filepath.Abs(pastaOrig)
	absComp, _ := filepath.Abs(pastaComp)

	dirIsEmpty := true
	if entries, err := os.ReadDir(absComp); err == nil && len(entries) > 0 {
		dirIsEmpty = false
	}
	if _, err := os.Stat(absComp); (os.IsNotExist(err) || dirIsEmpty) && !dryRun {
		fmt.Printf("📂 Criando pasta de destino: %s\n", absComp)
		os.MkdirAll(absComp, 0755)
		criarReadmeMinificado(absComp, absOrig)
	}

	// 1. Scan files
	var javaFiles []string
	var mdFiles []string
	var binaryFiles []string

	filepath.WalkDir(absOrig, func(path string, d os.DirEntry, err error) error {
		if err != nil { return nil }
		absPath, _ := filepath.Abs(path)
		
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
	})

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
		
		for _, fp := range javaFiles {
			relPath, _ := filepath.Rel(absOrig, fp)
			destPath := filepath.Join(absComp, relPath) + ".tknc"
			
			contentBytes, err := os.ReadFile(fp)
			if err != nil {
				continue
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
			sidecarBytes, _ := json.MarshalIndent(sidecar, "", "    ")
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
				os.MkdirAll(filepath.Dir(info.destPath), 0755)
				contentToSave := strings.ReplaceAll(finalContent, "\r\n", "\n")
				os.WriteFile(info.destPath, []byte(contentToSave), 0644)
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
			os.MkdirAll(tkndDir, 0755)
			criarReadmeTknd(tkndDir)
			for startID, sidecar := range sidecars {
				fileName := fmt.Sprintf("%s.cidatkn", startID)
				fileBytes, _ := json.MarshalIndent(sidecar, "", "    ")
				os.WriteFile(filepath.Join(tkndDir, fileName), fileBytes, 0644)
			}
			gerarScriptTraducao(absComp)
		}
		
		if len(javaMetrics) > 0 {
			javaMetricsJson, err := json.Marshal(javaMetrics)
			if err == nil {
				if dryRun {
					tempDir, err := os.MkdirTemp("", "cida_dryrun_*")
					if err == nil {
						tempJavaJsonPath = filepath.Join(tempDir, ".cida_java_raw.json")
						os.WriteFile(tempJavaJsonPath, javaMetricsJson, 0644)
						defer os.RemoveAll(tempDir)
					}
				} else {
					tempJavaJsonPath = filepath.Join(absComp, ".cida_java_raw.json")
					os.WriteFile(tempJavaJsonPath, javaMetricsJson, 0644)
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
		if failOnInflation {
			pyArgs = append(pyArgs, "--fail-on-inflation")
		}
		if verifySemantics {
			pyArgs = append(pyArgs, "--verify-semantics")
		}
		if dryRun {
			pyArgs = append(pyArgs, "--dry-run")
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
			os.MkdirAll(filepath.Dir(destPath), 0755)
			input, _ := os.ReadFile(bf)
			os.WriteFile(destPath, input, 0644)
		}
	}
}

func criarReadmeMinificado(pastaDestino string, pastaOrigem string) {
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
	
	os.WriteFile(filepath.Join(pastaDestino, "README_MINIFICADO.md"), []byte(conteudo), 0644)
	os.WriteFile(filepath.Join(pastaDestino, "CONSTITUTION.md"), []byte(getConstitutionContent()), 0644)
	os.WriteFile(filepath.Join(pastaDestino, "AGENTS.md"), []byte(getAgentsContent()), 0644)
	os.WriteFile(filepath.Join(pastaDestino, "PROMPT_INICIAL.MD"), []byte(getPromptInicialContent()), 0644)
}

func criarReadmeTknd(pastaTknd string) {
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
	os.WriteFile(filepath.Join(pastaTknd, "README.md"), []byte(conteudo), 0644)
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

func gerarScriptTraducao(pastaDestino string) {
	conteudo := getTranslatePyContent()
	os.WriteFile(filepath.Join(pastaDestino, "translate.py"), []byte(conteudo), 0755)
}

func construirDicionario(pastaOrig string, javaFiles []string, corpusHash string) (map[string]string, map[string]SidecarData) {
	contador := make(map[string]int)
	rePalavras := regexp.MustCompile(`\b[a-zA-Z_]{6,}\b`)

	for _, path := range javaFiles {
		conteudo, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		palavras := rePalavras.FindAllString(string(conteudo), -1)
		for _, p := range palavras {
			contador[p]++
		}
	}

	type kv struct { Key string; Freq int }
	var ss []kv
	for k, v := range contador { ss = append(ss, kv{k, v}) }
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
