package main

import (
	"context"
	"fmt"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/coder/websocket"
	"github.com/coder/websocket/wsjson"
	"mosswork.desktop/internal/company"
)

func TestInjectedCompanyJournalSurvivesClientReplacementWithoutGrants(t *testing.T) {
	journal := t.TempDir()
	folder, err := filepath.EvalSymlinks(t.TempDir())
	if err != nil {
		t.Fatal(err)
	}
	device := strings.Repeat("b", 32)
	replies := make(chan company.FileResult, 3)
	var connections atomic.Int32
	server := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		conn, err := websocket.Accept(w, r, nil)
		if err != nil {
			t.Error(err)
			return
		}
		defer conn.CloseNow()
		ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
		defer cancel()
		var hello map[string]any
		if err := wsjson.Read(ctx, conn, &hello); err != nil {
			t.Error(err)
			return
		}
		sequence := connections.Add(1)
		if err := wsjson.Write(ctx, conn, map[string]any{
			"type": "ready", "device_id": device, "owner_user_id": 7,
			"protocol_version": company.ProtocolVersion, "heartbeat_seconds": 15,
			"expires_at": time.Now().Add(time.Hour).Unix(), "credential": "mosswork-device.test",
		}); err != nil {
			t.Error(err)
			return
		}
		var permissions struct {
			Type   string          `json:"type"`
			Grants []company.Grant `json:"grants"`
		}
		if err := wsjson.Read(ctx, conn, &permissions); err != nil || permissions.Type != "grants" || len(permissions.Grants) != 1 {
			t.Errorf("grant update: %+v, %v", permissions, err)
			return
		}
		grant := permissions.Grants[0]
		if err := wsjson.Write(ctx, conn, map[string]any{
			"type": "call", "call_id": fmt.Sprintf("%032x", sequence),
			"task_id": strings.Repeat("a", 64), "device_id": device, "owner_user_id": 7,
			"agent_id": "expert", "thread_id": "thread", "grant_id": grant.ID,
			"grant_version": grant.Version, "tool": "local_write_file",
			"expires_at": time.Now().Add(time.Minute).Unix(),
			"arguments":  map[string]string{"path": fmt.Sprintf("report-%d.md", sequence), "content": "private journal test"},
		}); err != nil {
			t.Error(err)
			return
		}
		var result struct {
			Result company.FileResult `json:"result"`
		}
		if err := wsjson.Read(ctx, conn, &result); err != nil {
			t.Error(err)
			return
		}
		replies <- result.Result
		// Keep the real channel alive until the application replaces its client.
		var ignored any
		_ = wsjson.Read(ctx, conn, &ignored)
	}))
	defer server.Close()
	app := newApp(&settingsStore{cur: defaultSettings()}, journal)
	app.companyURL, app.workspaceNonce = server.URL, "nonce"
	t.Cleanup(func() { app.company.Close(false) })
	var previous *company.Client
	for index, action := range []string{"startup", "disconnect", "login"} {
		if !t.Run(action, func(t *testing.T) {
			if action == "disconnect" {
				if err := app.performWorkspaceAction("nonce", server.URL, "disconnect", ""); err != nil {
					t.Fatal(err)
				}
			} else if action == "login" {
				app.showLogin(false)
			}
			if app.company == previous || len(app.company.Grants()) != 0 {
				t.Fatal("replacement reused a client or inherited directory grants")
			}
			if previous != nil && len(previous.Grants()) != 0 {
				t.Fatal("previous client retained directory grants")
			}
			if err := app.company.Pair(context.Background(), server.URL, "mosswork-pair.test", "test", "darwin"); err != nil {
				t.Fatal(err)
			}
			if _, err := app.company.AddGrant(context.Background(), folder, true, true); err != nil {
				t.Fatal(err)
			}
			select {
			case result := <-replies:
				if result.Status != "completed" {
					t.Fatalf("actual file write failed: %+v", result)
				}
			case <-time.After(3 * time.Second):
				t.Fatal("file operation did not return")
			}
			id := fmt.Sprintf("%032x", index+1)
			if _, err := os.Stat(filepath.Join(journal, device, id+".json")); err != nil {
				t.Fatal("receipt escaped the injected journal", err)
			}
			output := filepath.Join(folder, fmt.Sprintf("report-%d.md", index+1))
			if data, err := os.ReadFile(output); err != nil || string(data) != "private journal test" {
				t.Fatal("real file was not written", string(data), err)
			}
			if path, err := app.company.ResultPath(id); err != nil || path != output {
				t.Fatal("receipt cannot resolve its authorized output", path, err)
			}
			if index > 0 {
				if _, err := app.company.ResultPath(fmt.Sprintf("%032x", 1)); err == nil {
					t.Fatal("new login inherited access to an earlier grant")
				}
			}
			previous = app.company
		}) {
			return
		}
	}
	entries, err := os.ReadDir(filepath.Join(journal, device))
	if err != nil || len(entries) != 3 {
		t.Fatal("client replacement lost prior durable receipts", len(entries), err)
	}
}
