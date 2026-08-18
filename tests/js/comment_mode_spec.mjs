/**
 * Behaviour spec for the comment overlay's lifecycle rules.
 *
 * Run: node tests/js/comment_mode_spec.mjs
 *
 * Three UX defects motivated this module, and each maps to one function here:
 *
 *  1. **The tool stayed armed after Enter.** Committing a comment left
 *     `state.shape === "comment"`, so the very next click dropped another one.
 *     modeAfterCommentCommit() is what the Enter handler now applies.
 *
 *  2. **A click destroyed typed text.** With the tool still armed, clicking
 *     away fell through to the canvas, which reset the pending point and
 *     blanked the textarea — the half-written comment was gone with no undo.
 *     shouldCanvasClickBeBlocked() is the guard.
 *
 *  3. **Backspace had to mean two things.** It must dismiss an overlay opened
 *     by mistake, yet still edit text once the user is writing. Keying it on
 *     the field's own emptiness (backspaceAction) rather than a mode flag is
 *     what keeps those from colliding — the destructive reading is
 *     unreachable while there is anything to destroy.
 *
 * The module imports nothing and touches no DOM, so no shim is needed.
 */
const url = new URL('../../frontend/js/comment-mode.js', import.meta.url);
const {
  shouldCanvasClickBeBlocked, backspaceAction, modeAfterCommentCommit
} = await import(url);

let pass = 0, fail = 0;
const ok = (name, cond) => {
  cond ? (pass++, console.log('  PASS', name)) : (fail++, console.log('  FAIL', name));
};

console.log('\nclick guard: typed text is protected');
{
  ok('open overlay with text blocks the click', shouldCanvasClickBeBlocked(true, 'needs review'));
  ok('a single character is enough to protect', shouldCanvasClickBeBlocked(true, 'x'));
  ok('leading/trailing spaces around text still protect',
    shouldCanvasClickBeBlocked(true, '  wording  '));
}

console.log('\nclick guard: nothing to lose lets the click through');
{
  ok('open but empty overlay does not block', !shouldCanvasClickBeBlocked(true, ''));
  ok('whitespace only is treated as empty', !shouldCanvasClickBeBlocked(true, '   '));
  ok('a newline only is treated as empty', !shouldCanvasClickBeBlocked(true, '\n'));
  ok('closed overlay never blocks', !shouldCanvasClickBeBlocked(false, 'text here'));
  ok('closed and empty never blocks', !shouldCanvasClickBeBlocked(false, ''));
}

console.log('\nclick guard: missing values are tolerated');
{
  ok('null text is not a crash', !shouldCanvasClickBeBlocked(true, null));
  ok('undefined text is not a crash', !shouldCanvasClickBeBlocked(true, undefined));
  ok('undefined open flag is falsy', !shouldCanvasClickBeBlocked(undefined, 'x'));
}

console.log('\nbackspace: cancel only from the untouched state');
{
  ok('empty field cancels the overlay', backspaceAction('') === 'cancel');
  ok('undefined behaves as empty', backspaceAction(undefined) === 'cancel');
  ok('null behaves as empty', backspaceAction(null) === 'cancel');
}

console.log('\nbackspace: never destroys a comment being written');
{
  ok('text edits normally', backspaceAction('a') === 'edit');
  ok('a long comment edits normally',
    backspaceAction('this needs a tighter bounding box') === 'edit');
  // The distinction that matters most: a user mid-word who has typed a space
  // must keep Backspace as an editing key. Trimming here would have made a
  // whitespace-only field "empty" and closed the overlay under them.
  ok('a single space is editing, not cancelling', backspaceAction(' ') === 'edit');
  ok('whitespace only is editing, not cancelling', backspaceAction('   ') === 'edit');
  ok('a newline is editing, not cancelling', backspaceAction('\n') === 'edit');
}

console.log('\nbackspace and the click guard disagree on whitespace, deliberately');
{
  // Same input, opposite answers, and both are right: a click discards the
  // whole overlay (nothing of value is lost when only spaces are typed), while
  // Backspace only erases one character (which the user is entitled to do).
  ok('whitespace: click may pass through', !shouldCanvasClickBeBlocked(true, '  '));
  ok('whitespace: backspace still edits', backspaceAction('  ') === 'edit');
}

console.log('\nmode after commit');
{
  const next = modeAfterCommentCommit();
  ok('disarms to select so a stray click cannot create', next.mode === 'select');
  ok('shape returns to polygon', next.shape === 'polygon');
  ok('never stays on comment', next.shape !== 'comment');
  // (b) was considered and rejected: arming draw mode would let a stray click
  // right after typing start a polygon - the same accident, relocated.
  ok('does not re-arm draw mode', next.mode !== 'draw');
  ok('returns a fresh object each call',
    modeAfterCommentCommit() !== modeAfterCommentCommit());
}

console.log(`\n${pass} passed, ${fail} failed`);
process.exit(fail === 0 ? 0 : 1);
