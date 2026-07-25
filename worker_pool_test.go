package main

import (
	"context"
	"errors"
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
