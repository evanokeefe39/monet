// Package tui is the Bubble Tea chat TUI for monet-tui.
package tui

import (
	"context"
	"fmt"
	"strings"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/evanokeefe39/monet-tui/internal/chatclient"
	"github.com/evanokeefe39/monet-tui/internal/monetclient"
	"github.com/evanokeefe39/monet-tui/internal/wire"
)

// Mode tracks the current input mode.
type Mode int

const (
	ModeChat     Mode = iota // normal chat input
	ModeHITL                // waiting for HITL response
	ModePicker              // inline picker active
	ModeForm                // Huh form active
	ModeThreads             // thread picker active
	ModeQuit                // quit confirmation (two-press)
)

// Model is the root Bubble Tea model.
type Model struct {
	client      *chatclient.Client
	mclient     *monetclient.Client
	threadID    string
	runID       string
	lastEventID string

	width  int
	height int
	mode   Mode

	transcript Transcript
	input      Input
	inline     InlinePick
	form       FormModel
	threads    ThreadPicker
	quit       QuitModel
	log        Logger
	clipboard  ClipboardWriter

	ctx    context.Context
	cancel context.CancelFunc

	slashCmds []chatclient.SlashCommand
	pending   *wire.Interrupt
}

// New creates the initial TUI Model.
func New(
	ctx context.Context,
	client *chatclient.Client,
	mclient *monetclient.Client,
	logDir string,
	clipboardMode string,
) (Model, error) {
	childCtx, cancel := context.WithCancel(ctx)
	logger, err := NewLogger(logDir)
	if err != nil {
		cancel()
		return Model{}, err
	}

	m := Model{
		client:    client,
		mclient:   mclient,
		ctx:       childCtx,
		cancel:    cancel,
		mode:      ModeChat,
		log:       logger,
		clipboard: NewClipboardWriter(clipboardMode),
		transcript: NewTranscript(),
		input:      NewInput(),
		quit:       NewQuitModel(),
	}
	return m, nil
}

// ─── Bubble Tea interface ─────────────────────────────────────────────────────

func (m Model) Init() tea.Cmd {
	cmds := []tea.Cmd{
		m.input.Init(),
		loadSlashCmds(m.client, m.ctx),
	}
	return tea.Batch(cmds...)
}

func (m Model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.width = msg.Width
		m.height = msg.Height
		m.transcript.SetSize(msg.Width, msg.Height-inputHeight-statusHeight)
		m.input.SetWidth(msg.Width)
		return m, nil

	case tea.KeyMsg:
		return m.handleKey(msg)

	case slashCmdsMsg:
		m.slashCmds = msg.cmds
		m.input.SetSlashCommands(msg.cmds)
		return m, nil

	case runEventMsg:
		return m.handleRunEvent(msg.ev)

	case runEndMsg:
		return m.handleRunEnd(msg.err)

	case threadCreatedMsg:
		m.threadID = msg.id
		m.transcript.AddInfo(fmt.Sprintf("Thread created: %s", msg.id))
		return m, nil

	case threadListMsg:
		m.threads = NewThreadPicker(msg.threads)
		m.mode = ModeThreads
		return m, m.threads.Init()

	case errorMsg:
		m.transcript.AddError(msg.err.Error())
		m.mode = ModeChat
		return m, nil
	}

	return m.delegateUpdate(msg)
}

func (m Model) View() string {
	switch m.mode {
	case ModeThreads:
		return m.threads.View()
	case ModeQuit:
		return m.quit.View()
	case ModeForm:
		return lipgloss.JoinVertical(lipgloss.Left,
			m.transcript.View(),
			m.form.View(),
		)
	}

	status := m.statusBar()
	return lipgloss.JoinVertical(lipgloss.Left,
		m.transcript.View(),
		m.renderHITL(),
		m.input.View(),
		status,
	)
}

// ─── Key handling ─────────────────────────────────────────────────────────────

const (
	inputHeight  = 3
	statusHeight = 1
)

func (m Model) handleKey(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.Type {
	case tea.KeyCtrlC:
		if m.mode == ModeQuit {
			m.cancel()
			return m, tea.Quit
		}
		m.mode = ModeQuit
		return m, m.quit.Start()

	case tea.KeyCtrlX:
		if m.runID != "" {
			return m, cancelRun(m.client, m.ctx, m.threadID, m.runID)
		}
		return m, nil

	case tea.KeyEnter:
		if m.mode == ModeHITL || m.mode == ModePicker {
			return m.submitHITL()
		}
		if m.mode == ModeChat {
			return m.submitMessage()
		}
	}

	return m.delegateUpdate(msg)
}

func (m Model) delegateUpdate(msg tea.Msg) (tea.Model, tea.Cmd) {
	var cmds []tea.Cmd
	switch m.mode {
	case ModeChat:
		var cmd tea.Cmd
		m.input, cmd = m.input.Update(msg)
		cmds = append(cmds, cmd)
	case ModeHITL, ModePicker:
		var cmd tea.Cmd
		m.inline, cmd = m.inline.Update(msg)
		cmds = append(cmds, cmd)
	case ModeForm:
		var cmd tea.Cmd
		m.form, cmd = m.form.Update(msg)
		cmds = append(cmds, cmd)
	case ModeThreads:
		var cmd tea.Cmd
		m.threads, cmd = m.threads.Update(msg)
		cmds = append(cmds, cmd)
	case ModeQuit:
		var cmd tea.Cmd
		m.quit, cmd = m.quit.Update(msg)
		cmds = append(cmds, cmd)
	}
	return m, tea.Batch(cmds...)
}

// ─── Submit ───────────────────────────────────────────────────────────────────

func (m Model) submitMessage() (tea.Model, tea.Cmd) {
	text := strings.TrimSpace(m.input.Value())
	if text == "" {
		return m, nil
	}
	m.input.Reset()

	// Handle slash commands.
	if strings.HasPrefix(text, "/") {
		return m.handleSlash(text)
	}

	// Ensure thread exists.
	if m.threadID == "" {
		return m, tea.Batch(
			createThread(m.client, m.ctx, ""),
			func() tea.Msg { return pendingMessageMsg{text: text} },
		)
	}

	m.transcript.AddUser(text)
	return m, streamMessage(m.client, m.ctx, m.threadID, text, m.lastEventID)
}

func (m Model) submitHITL() (tea.Model, tea.Cmd) {
	if m.pending == nil {
		m.mode = ModeChat
		return m, nil
	}
	payload := m.inline.Payload()
	m.mode = ModeChat
	m.pending = nil
	m.transcript.AddInfo("Submitting decision...")
	return m, resumeRun(m.client, m.ctx, m.threadID, "hitl", payload, m.lastEventID)
}

// ─── Run event handling ───────────────────────────────────────────────────────

func (m Model) handleRunEvent(ev wire.RunEvent) (tea.Model, tea.Cmd) {
	switch ev.Kind {
	case wire.RunEventStarted:
		m.runID = ev.Started.RunID
		m.transcript.AddInfo(fmt.Sprintf("[run %s started]", truncate(m.runID, 8)))

	case wire.RunEventUpdate:
		if ev.Update != nil {
			msgs := wire.ExtractAssistantMessages(ev.Update.Update)
			for _, msg := range msgs {
				m.transcript.AddAssistant(msg)
			}
		}

	case wire.RunEventProgress:
		if ev.Progress != nil {
			m.transcript.AddProgress(ev.Progress)
		}

	case wire.RunEventInterrupt:
		if ev.Interrupt != nil {
			m.pending = ev.Interrupt
			return m.enterHITL(ev.Interrupt)
		}

	case wire.RunEventComplete:
		m.runID = ""
		m.transcript.AddInfo("[run complete]")
		_ = m.log.LogEvent("run_complete", ev.Complete)

	case wire.RunEventFailed:
		m.runID = ""
		if ev.Failed != nil {
			m.transcript.AddError("Run failed: " + ev.Failed.Error)
		}
	}
	return m, nil
}

func (m Model) handleRunEnd(err error) (tea.Model, tea.Cmd) {
	m.runID = ""
	if err != nil {
		m.transcript.AddError("Stream error: " + err.Error())
	}
	m.mode = ModeChat
	return m, nil
}

func (m Model) enterHITL(interrupt *wire.Interrupt) (Model, tea.Cmd) {
	vals := interrupt.Values
	form, isForm := wire.InterruptValue(vals)
	if isForm && InlinePickProtocol.Matches(form) {
		shape := InlinePickProtocol.Extract(form)
		m.inline = NewInlinePick(shape)
		m.mode = ModePicker
		return m, m.inline.Init()
	}
	if isForm {
		m.form = NewFormModel(form)
		m.mode = ModeForm
		return m, m.form.Init()
	}
	// Generic: show raw values and accept typed text reply.
	m.transcript.AddInterrupt(interrupt)
	m.mode = ModeHITL
	return m, nil
}

// ─── Slash handling ───────────────────────────────────────────────────────────

func (m Model) handleSlash(cmd string) (tea.Model, tea.Cmd) {
	switch cmd {
	case "/threads":
		return m, loadThreads(m.client, m.ctx, 50)
	case "/artifacts":
		if m.threadID == "" {
			m.transcript.AddError("no active thread")
			return m, nil
		}
		return m, loadArtifacts(m.mclient, m.ctx, m.threadID)
	case "/cancel":
		if m.runID != "" {
			return m, cancelRun(m.client, m.ctx, m.threadID, m.runID)
		}
		m.transcript.AddInfo("no in-flight run")
		return m, nil
	case "/help":
		m.transcript.AddHelp(m.slashCmds)
		return m, nil
	case "/about":
		m.transcript.AddInfo(aboutText())
		return m, nil
	case "/runs":
		return m, loadRuns(m.mclient, m.ctx, 20)
	case "/themes":
		m.transcript.AddInfo(FormatThemesHelp())
		return m, nil
	case "/colors":
		path, err := WriteDefaultColors()
		if err != nil {
			m.transcript.AddError("write colors: " + err.Error())
			return m, nil
		}
		m.transcript.AddInfo("colors config: " + path)
		return m, nil
	default:
		m.transcript.AddError("unknown command: " + cmd)
		return m, nil
	}
}

func aboutText() string {
	return "monet-tui — Bubble Tea chat TUI for monet. Keys: Enter send · ^C quit · ^X cancel · /help commands · /threads switch · /runs history · /artifacts list · /cancel abort"
}

// ─── Status bar ───────────────────────────────────────────────────────────────

var statusStyle = lipgloss.NewStyle().
	Foreground(lipgloss.Color("241")).
	PaddingLeft(1)

func (m Model) statusBar() string {
	parts := []string{}
	if m.threadID != "" {
		parts = append(parts, "thread:"+truncate(m.threadID, 8))
	}
	if m.runID != "" {
		parts = append(parts, "run:"+truncate(m.runID, 8)+" ▶")
	}
	parts = append(parts, "^C quit · ^X cancel")
	return statusStyle.Width(m.width).Render(strings.Join(parts, "  ·  "))
}

func (m Model) renderHITL() string {
	if m.mode != ModeHITL {
		return ""
	}
	if m.pending == nil {
		return ""
	}
	return infoStyle.Render("[interrupt] type a reply and press Enter")
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
