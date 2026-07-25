package main

import (
	"context"
	"errors"
	"fmt"
	"runtime"
	"sync/atomic"
	"testing"
	"time"
)

func TestRunPoolProcessesAllJobsInOrder(t *testing.T) {
	var jobs []Job[int]
	for i := 0; i < 100; i++ {
		jobs = append(jobs, Job[int]{Index: i, Value: i})
	}

	results := RunPool(context.Background(), 10, jobs, func(_ context.Context, value int) (int, error) {
		return value * 2, nil
	})

	for i, result := range results {
		if result.Index != i || result.Value != i*2 || result.Err != nil {
			t.Fatalf("unexpected result at %d: %+v", i, result)
		}
	}
}

func TestRunPoolMaximumConcurrency(t *testing.T) {
	var active int32
	var maxActive int32
	var jobs []Job[int]
	for i := 0; i < 40; i++ {
		jobs = append(jobs, Job[int]{Index: i, Value: i})
	}

	RunPool(context.Background(), 4, jobs, func(_ context.Context, value int) (int, error) {
		now := atomic.AddInt32(&active, 1)
		for {
			prev := atomic.LoadInt32(&maxActive)
			if now <= prev || atomic.CompareAndSwapInt32(&maxActive, prev, now) {
				break
			}
		}
		time.Sleep(time.Millisecond)
		atomic.AddInt32(&active, -1)
		return value, nil
	})

	if maxActive > 4 {
		t.Fatalf("max concurrency = %d; want <= 4", maxActive)
	}
}

func TestRunPoolErrorAndPanicHandling(t *testing.T) {
	jobs := []Job[int]{{Index: 0, Value: 0}, {Index: 1, Value: 1}, {Index: 2, Value: 2}}
	results := RunPoolWithOptions(context.Background(), 2, jobs, func(_ context.Context, value int) (int, error) {
		if value == 1 {
			return 0, errors.New("boom")
		}
		if value == 2 {
			panic("bad")
		}
		return value, nil
	}, PoolOptions{ContinueOnError: true})

	if results[0].Err != nil {
		t.Fatalf("unexpected error: %v", results[0].Err)
	}
	if results[1].Err == nil || results[2].Err == nil {
		t.Fatalf("expected error and panic to be captured: %+v", results)
	}
}

func TestRunPoolCancellationAndNoLeak(t *testing.T) {
	before := runtime.NumGoroutine()
	ctx, cancel := context.WithCancel(context.Background())
	jobs := []Job[int]{{Index: 0, Value: 0}, {Index: 1, Value: 1}, {Index: 2, Value: 2}}

	results := RunPool(ctx, 2, jobs, func(ctx context.Context, value int) (int, error) {
		if value == 0 {
			cancel()
			return 0, context.Canceled
		}
		<-ctx.Done()
		return 0, ctx.Err()
	})

	if results[0].Err == nil {
		t.Fatalf("expected cancellation error: %+v", results)
	}
	time.Sleep(20 * time.Millisecond)
	after := runtime.NumGoroutine()
	if after > before+2 {
		t.Fatalf("possible goroutine leak: before=%d after=%d", before, after)
	}
}

func TestRunPoolRejectsInvalidJobIndexes(t *testing.T) {
	tests := []struct {
		name string
		jobs []Job[int]
	}{
		{name: "negative", jobs: []Job[int]{{Index: -1, Value: 1}}},
		{name: "too-large", jobs: []Job[int]{{Index: 1, Value: 1}}},
		{name: "duplicate", jobs: []Job[int]{{Index: 0, Value: 1}, {Index: 0, Value: 2}}},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			outcome := RunPoolOutcomeWithOptions(context.Background(), 2, tt.jobs, func(_ context.Context, value int) (int, error) {
				return value, nil
			}, PoolOptions{})
			var configErr *PoolConfigurationError
			if !errors.As(outcome.RootError, &configErr) {
				t.Fatalf("expected configuration root error, got %v", outcome.RootError)
			}
			for _, result := range outcome.Results {
				if !errors.As(result.Err, &configErr) {
					t.Fatalf("expected result configuration error, got %+v", outcome.Results)
				}
			}
		})
	}
}

func TestRunPoolAllowsOutOfOrderLogicalIndexes(t *testing.T) {
	jobs := []Job[int]{{Index: 2, Value: 20}, {Index: 0, Value: 0}, {Index: 1, Value: 10}}

	results := RunPool(context.Background(), 2, jobs, func(_ context.Context, value int) (int, error) {
		return value + 1, nil
	})

	for position, result := range results {
		if result.Index != jobs[position].Index || result.Value != jobs[position].Value+1 || result.Err != nil {
			t.Fatalf("unexpected result at position %d: %+v", position, result)
		}
	}
}

func TestRunPoolZeroJobsAndMoreWorkersThanJobs(t *testing.T) {
	empty := RunPool(context.Background(), 10, []Job[int]{}, func(_ context.Context, value int) (int, error) {
		return value, nil
	})
	if len(empty) != 0 {
		t.Fatalf("expected no results for zero jobs, got %+v", empty)
	}

	jobs := []Job[int]{{Index: 0, Value: 1}, {Index: 1, Value: 2}}
	results := RunPool(context.Background(), 20, jobs, func(_ context.Context, value int) (int, error) {
		return value * 3, nil
	})
	if len(results) != len(jobs) {
		t.Fatalf("unexpected result length: %+v", results)
	}
}

func TestRunPoolOutcomePreservesRootFailureOverDerivedCancellation(t *testing.T) {
	rootFailure := &TokenizerProcessingError{Path: "src/B.java", Err: errors.New("bad token")}
	jobs := []Job[int]{{Index: 0, Value: 0}, {Index: 1, Value: 1}, {Index: 2, Value: 2}}

	outcome := RunPoolOutcomeWithOptions(context.Background(), 3, jobs, func(ctx context.Context, value int) (int, error) {
		if value == 1 {
			return 0, rootFailure
		}
		<-ctx.Done()
		return 0, ctx.Err()
	}, PoolOptions{})

	var tokenizerErr *TokenizerProcessingError
	if !errors.As(outcome.RootError, &tokenizerErr) {
		t.Fatalf("expected tokenizer root error, got %T %v", outcome.RootError, outcome.RootError)
	}
	if fmt.Sprint(outcome.Results) == "" {
		t.Fatal("results should be defined")
	}
}

func TestJavaProcessingExitCodesUseTypedErrors(t *testing.T) {
	if got := exitCodeForJavaProcessingError(&TokenizerProcessingError{Path: "A.java", Err: errors.New("x")}); got != 2 {
		t.Fatalf("tokenizer exit code = %d; want 2", got)
	}
	if got := exitCodeForJavaProcessingError(&SourceIOProcessingError{Path: "A.java", Err: errors.New("x")}); got != 4 {
		t.Fatalf("source exit code = %d; want 4", got)
	}
	if got := exitCodeForJavaProcessingError(&WorkerPanicError{Value: "x"}); got != 6 {
		t.Fatalf("panic exit code = %d; want 6", got)
	}
}
