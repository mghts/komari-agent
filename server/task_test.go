package server

import (
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
	"time"
)

var testTargets = []struct {
	target string
}{
	{"v6-sh-cm.oojj.de"},
	{"2409:8c1e:8f80:2:6a::"},
	{"[2409:8c1e:8f80:2:6a::]"},
	{"[2409:8c1e:8f80:2:6a::]:80"},
	{"v4-sh-cm.oojj.de"},
	{"117.185.125.154"},
	{"117.185.125.154:80"},
}

func TestICMPPing(t *testing.T) {
	requireExternalPingTests(t)
	timeout := 3 * time.Second
	for _, tt := range testTargets {
		t.Run(tt.target, func(t *testing.T) {
			latency, err := icmpPing(tt.target, timeout)
			if latency < -1 {
				t.Errorf("ICMP ping %s: invalid latency %d", tt.target, latency)
			}
			if err != nil {
				t.Errorf("ICMP ping %s error: %v", tt.target, err)
			}
		})
	}
}

func TestTCPPing(t *testing.T) {
	requireExternalPingTests(t)
	timeout := 3 * time.Second
	for _, tt := range testTargets {
		t.Run(tt.target, func(t *testing.T) {
			latency, err := tcpPing(tt.target, timeout)
			if latency < -1 {
				t.Errorf("TCP ping %s: invalid latency %d", tt.target, latency)
			}
			if err != nil {
				t.Errorf("TCP ping %s error: %v", tt.target, err)
			}
		})
	}
}

func TestHTTPPing(t *testing.T) {
	requireExternalPingTests(t)
	timeout := 3 * time.Second
	for _, tt := range testTargets {
		t.Run(tt.target, func(t *testing.T) {
			latency, err := httpPing(tt.target, timeout)
			if latency < -1 {
				t.Errorf("HTTP ping %s: invalid latency %d", tt.target, latency)
			}
			if err != nil {
				t.Errorf("HTTP ping %s error: %v", tt.target, err)
			}
		})
	}
}

func requireExternalPingTests(t *testing.T) {
	t.Helper()
	if os.Getenv("KOMARI_EXTERNAL_PING_TESTS") != "1" {
		t.Skip("set KOMARI_EXTERNAL_PING_TESTS=1 to test the historical public IPv4/IPv6 endpoints")
	}
}

func TestLocalTCPAndHTTPPing(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
	}))
	t.Cleanup(server.Close)
	address := strings.TrimPrefix(server.URL, "http://")
	if latency, err := tcpPing(address, time.Second); err != nil || latency < 0 {
		t.Fatalf("local TCP ping: latency=%d err=%v", latency, err)
	}
	if latency, err := httpPing(server.URL, time.Second); err != nil || latency < 0 {
		t.Fatalf("local HTTP ping: latency=%d err=%v", latency, err)
	}
	server.Close()
	if _, err := tcpPing(address, time.Second); err == nil {
		t.Fatal("closed TCP listener must fail")
	}
}

func TestLocalHTTPPingRejectsServerError(t *testing.T) {
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	t.Cleanup(server.Close)
	if _, err := httpPing(server.URL, time.Second); err == nil {
		t.Fatal("HTTP 503 must not be reported as a successful ping")
	}
}

func TestLocalICMPPing(t *testing.T) {
	latency, err := icmpPing("127.0.0.1", time.Second)
	if err != nil && (strings.Contains(err.Error(), "operation not permitted") || strings.Contains(err.Error(), "permission denied")) {
		t.Skip("ICMP test requires CAP_NET_RAW")
	}
	if err != nil || latency < 0 {
		t.Fatalf("local ICMP ping: latency=%d err=%v", latency, err)
	}
}
