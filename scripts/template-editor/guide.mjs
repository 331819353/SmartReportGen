import { Editor, commandsCtx, defaultValueCtx, editorViewCtx, editorViewOptionsCtx, rootCtx, serializerCtx } from '@milkdown/core';
import { history } from '@milkdown/plugin-history';
import { commonmark, imageAttr } from '@milkdown/preset-commonmark';
import { addRowAfterCommand, addRowBeforeCommand, createTable, exitTable, gfm } from '@milkdown/preset-gfm';
import { closeHistory, undoDepth, redoDepth, undo, redo } from '@milkdown/prose/history';
import { toggleMark } from '@milkdown/prose/commands';
import { liftListItem, sinkListItem, splitListItem } from '@milkdown/prose/schema-list';
import { Selection } from '@milkdown/prose/state';
import { addColumnAfter, addColumnBefore, deleteColumn, deleteTable, goToNextCell, isInTable, selectedRect, selectionCell } from '@milkdown/prose/tables';
import { Decoration, DecorationSet } from '@milkdown/prose/view';
import { unified } from 'unified';
import remarkParse from 'remark-parse';
import remarkGfm from 'remark-gfm';
import remarkRehype from 'remark-rehype';
import rehypeSanitize from 'rehype-sanitize';
import rehypeStringify from 'rehype-stringify';
import {
  activeList, alignColumns, changeCode, changeList, exitCodeBlock, insertDivider, linkAtSelection, removeTableRows, setHeading, setLink, toggleQuote
} from './format.mjs';
import '@milkdown/prose/view/style/prosemirror.css';
import '@milkdown/prose/tables/style/tables.css';

// 阅读页先清理 Markdown 生成的 HTML，再加上与编辑态对应的滚动容器和代码语言标记。
function formatPreview() {
  return function wrapBlocks(parent) {
    parent.children?.forEach((child, index) => {
      const element = (tagName, properties, children) => ({ type: 'element', tagName, properties, children });
      if (child.tagName === 'table') {
        parent.children[index] = element('div', { className: ['template-table-scroll'], tabIndex: 0, role: 'region', ariaLabel: '正文表格' }, [child]);
      } else if (child.tagName === 'pre') {
        const language = child.children[0]?.properties?.className?.find(name => name.startsWith('language-'))?.slice(9);
        if (language) child.properties.dataLanguage = language;
        child.properties.tabIndex = 0;
      }
      wrapBlocks(child);
    });
  };
}

const markdownParser = unified().use(remarkParse).use(remarkGfm).freeze();
const renderer = markdownParser().use(remarkRehype, { clobberPrefix: '', footnoteLabel: '脚注' })
  .use(rehypeSanitize).use(formatPreview).use(rehypeStringify).freeze();

export function renderGuide(markdown) {
  return String(renderer.processSync(markdown));
}

function containsDefinition(node) {
  return node.type === 'definition' || node.children?.some(containsDefinition);
}

const taskDecorations = new WeakMap();
function taskWidgets(state) {
  if (taskDecorations.has(state.doc)) return taskDecorations.get(state.doc);
  const widgets = [];
  state.doc.descendants((node, pos) => {
    if (node.type.name !== 'list_item' || node.attrs.checked == null) return;
    const checked = node.attrs.checked;
    widgets.push(Decoration.widget(pos + 2, (view, getPos) => {
      const box = document.createElement('input');
      box.type = 'checkbox';
      box.checked = checked;
      box.className = 'template-task-checkbox';
      box.setAttribute('aria-label', '完成任务：' + (node.firstChild.textContent || '未命名任务'));
      box.addEventListener('change', () => {
        // Widget 可以随编辑移动，勾选时按当前解析位置查找对应的列表项。
        if (!view.editable) { box.checked = checked; return; }
        const at = view.state.doc.resolve(getPos());
        view.dispatch(view.state.tr.setNodeAttribute(at.before(at.depth - 1), 'checked', box.checked));
        view.focus();
      });
      return box;
    }, { side: -1, stopEvent: () => true, ignoreSelection: true }));
  });
  const decorations = DecorationSet.create(state.doc, widgets);
  taskDecorations.set(state.doc, decorations);
  return decorations;
}

function documentDecorations(state) {
  const tasks = taskWidgets(state);
  if (!state.selection.empty || !isInTable(state)) return tasks;
  const cell = selectionCell(state);
  return tasks.add(state.doc, [Decoration.node(cell.pos, cell.pos + cell.nodeAfter.nodeSize, { class: 'template-active-cell' })]);
}

function slashAtSelection(state) {
  const { $cursor } = state.selection;
  if (!$cursor || $cursor.parent.type.name !== 'paragraph' || isInTable(state)) return null;
  const prefix = $cursor.parent.textBetween(0, $cursor.parentOffset, '\n', '\ufffc');
  const match = /^\/([\p{L}\p{N} _-]*)$/u.exec(prefix);
  return match ? { from: $cursor.start(), to: $cursor.pos, query: match[1] } : null;
}

function toolbarState(state) {
  const { $from, $to, from, to, empty } = state.selection;
  const marks = state.storedMarks || $from.marks();
  const hasMark = type => empty ? Boolean(type.isInSet(marks)) : state.doc.rangeHasMark(from, to, type);
  const { strong, emphasis, strike_through, inlineCode } = state.schema.marks;
  const list = activeList(state.selection);
  const inList = Boolean($from.blockRange($to, node => node.type.name === 'list_item'));
  const table = isInTable(state);
  const rect = table ? selectedRect(state) : null;
  const code = $from.sameParent($to) && $from.parent.type.spec.code;
  return {
    heading: $from.parent.type.name === 'heading' ? $from.parent.attrs.level : 0,
    bold: hasMark(strong),
    italic: hasMark(emphasis),
    strike: hasMark(strike_through),
    inlineCode: hasMark(inlineCode),
    link: code ? '' : linkAtSelection(state),
    quote: Boolean($from.blockRange($to, node => node.type.name === 'blockquote')),
    bullet: list === 'bullet',
    ordered: list === 'ordered',
    task: list === 'task',
    indent: inList && sinkListItem(state.schema.nodes.list_item)(state),
    outdent: inList && liftListItem(state.schema.nodes.list_item)(state),
    code: Boolean(code),
    codeLanguage: code ? $from.parent.attrs.language : '',
    table,
    aboveHeader: rect?.top === 0,
    cannotDeleteRow: rect ? rect.map.height - (rect.bottom - rect.top) < 2 : false,
    minCols: rect ? rect.map.width === rect.right - rect.left : false,
    tableAlign: rect ? rect.table.firstChild.child(rect.left).attrs.alignment || 'left' : '',
    undo: undoDepth(state) > 0,
    redo: redoDepth(state) > 0,
    slash: slashAtSelection(state),
  };
}

// 斜杠菜单在同一步撤销中消费命令文字并应用格式；命令不可用时保留用户输入。
function runAfterDeleting(view, target, command) {
  const tr = view.state.tr.delete(target.from, target.to);
  return command(view.state.apply(tr), next => {
    next.steps.forEach(step => tr.step(step));
    tr.setSelection(Selection.fromJSON(tr.doc, next.selection.toJSON()));
    view.dispatch(tr.scrollIntoView());
  });
}

function openCodeFence(view, $from, to) {
  if ($from.parent.type.name !== 'paragraph' || isInTable(view.state)) return false;
  const prefix = $from.parent.textBetween(0, $from.parentOffset, '\n', '\ufffc');
  const fence = /^(?:```|~~~)([\w+.-]*)$/.exec(prefix);
  if (!fence) return false;
  // 空格和回车都能完成代码围栏输入，包括 SQL、C++ 等语言标记。
  return runAfterDeleting(view, { from: $from.start(), to }, (state, dispatch) => changeCode(state, dispatch, fence[1]));
}

export async function createGuideEditor(root, markdown, onMarkdown, onSelection) {
  // 编辑器会把引用式链接展成内联链接并丢弃定义，保留这类文档的源码编辑能力。
  if (containsDefinition(markdownParser.parse(markdown))) return null;

  let initialDoc;
  const editor = Editor.make().config(ctx => {
    ctx.set(rootCtx, root);
    ctx.set(defaultValueCtx, markdown);
    ctx.set(imageAttr.key, () => ({ referrerpolicy: 'no-referrer', loading: 'lazy' }));
    ctx.set(editorViewOptionsCtx, {
      attributes: { role: 'textbox', 'aria-label': '模板正文', 'aria-multiline': 'true' },
      decorations: documentDecorations,
      nodeViews: {
        table() {
          // 编辑与阅读均由外层容器负责横向滚动，表格本身保持原生单元格布局。
          const dom = document.createElement('div');
          dom.className = 'template-table-scroll';
          const table = dom.appendChild(document.createElement('table'));
          return { dom, contentDOM: table.appendChild(document.createElement('tbody')) };
        },
      },
      handleClick(view, _pos, event) {
        // 编辑时点击链接用于移动光标，实际跳转留给阅读态。
        if (!event.target.closest('a')) return false;
        event.preventDefault();
        return true;
      },
      handleTextInput(view, from, to, text) {
        const $from = view.state.doc.resolve(from);
        if (text === ' ' && openCodeFence(view, $from, to)) return true;
        if (text !== ' ' || $from.parent.type.name !== 'paragraph' || isInTable(view.state)
          || $from.blockRange($from, node => node.type.name === 'list_item')) return false;
        const prefix = $from.parent.textBetween(0, $from.parentOffset);
        if (!/^\[[ xX]?\]$/.test(prefix)) return false;
        return runAfterDeleting(view, { from: $from.start(), to }, (state, dispatch) => changeList(state, next => {
          if (/x/i.test(prefix)) next.setNodeAttribute(next.selection.$from.before(-1), 'checked', true);
          dispatch(next);
        }, 'task'));
      },
      handleKeyDown(view, event) {
        if (event.isComposing || !view.editable) return false;
        const { state, dispatch } = view;
        const { $head } = state.selection;
        const mod = event.metaKey || event.ctrlKey;
        const run = (command, ...args) => command(state, dispatch, ...args);
        const preset = cmd => ctx.get(commandsCtx).call(cmd.key);
        if (mod && event.altKey && ['Digit7', 'Digit8', 'KeyC'].includes(event.code)) {
          if (event.code === 'KeyC') return run(changeCode);
          return run(changeList, event.code === 'Digit7' ? 'ordered' : 'bullet');
        }
        if (isInTable(state)) {
          if (event.key === 'Enter' && mod) return preset(exitTable);
          if (event.key === 'Tab' || (event.key === 'Enter' && !event.shiftKey)) {
            const next = goToNextCell(event.shiftKey ? -1 : 1);
            if (run(next)) return true;
            if (event.shiftKey) return false;
            // 表格最后一格按 Tab 时自动添加一行；默认 Enter 不会意外退出表格。
            if (event.key === 'Tab' && preset(addRowAfterCommand)) next(view.state, dispatch);
            return true;
          }
        }
        if (event.key === 'Enter' && $head.parent.type.spec.code) {
          if (mod) return run(exitCodeBlock);
          view.dispatch(state.tr.insertText('\n').scrollIntoView());
          return true;
        }
        if (event.key === 'Enter' && !event.shiftKey && !mod && state.selection.empty
          && openCodeFence(view, $head, $head.pos)) return true;
        if (event.key === 'Enter' && !event.shiftKey && !mod && $head.depth >= 2) {
          const item = $head.node(-1);
          if (item.type === state.schema.nodes.list_item) {
            // 回车延续当前列表，复选项总是从未勾选开始，空项回车退出当前层级。
            if ($head.parent.content.size === 0 && item.childCount === 1) return run(liftListItem(item.type));
            if (item.attrs.checked != null) return run(splitListItem(item.type, { ...item.attrs, checked: false }));
          }
        }
        return false;
      },
      dispatchTransaction(transaction) {
        const updated = this.state.apply(transaction);
        const changed = updated.doc !== this.state.doc;
        this.updateState(updated);
        onSelection(toolbarState(this.state));
        if (changed && initialDoc) {
          const doc = this.state.doc;
          // 打开、移动光标及撤销回初始内容时保留原始文本，避免空改动产生模板版本。
          onMarkdown(doc.eq(initialDoc) ? markdown : ctx.get(serializerCtx)(doc));
        }
      },
    });
  }).use(commonmark).use(gfm).use(history);

  await editor.create();
  const view = editor.ctx.get(editorViewCtx);
  initialDoc = view.state.doc;
  onSelection(toolbarState(view.state));

  function insertTable(state, dispatch) {
    const table = editor.action(ctx => createTable(ctx, 3, 2));
    const { $from } = state.selection;
    // 只替换空段；有文字时在其后插入，避免误删已经选中的正文。
    const at = $from.depth ? $from.after() : $from.pos;
    const tr = state.tr;
    if ($from.parent.type.name === 'paragraph' && !$from.parent.content.size) {
      tr.replaceRangeWith($from.before(), at, table);
    } else {
      tr.insert(at, table);
    }
    let inserted = false;
    tr.doc.descendants((node, pos) => {
      if (node !== table) return true;
      const cell = Selection.findFrom(tr.doc.resolve(pos + 1), 1, true);
      if (cell) tr.setSelection(cell);
      inserted = true;
      return false;
    });
    if (inserted) dispatch(tr.scrollIntoView());
    return inserted;
  }

  const preset = cmd => editor.ctx.get(commandsCtx).get(cmd.key)();
  const mark = name => () => toggleMark(view.state.schema.marks[name]);
  const list = style => () => (state, dispatch) => changeList(state, dispatch, style);
  const commands = {
    heading: level => (state, dispatch) => setHeading(state, dispatch, level),
    bold: mark('strong'),
    italic: mark('emphasis'),
    strike: mark('strike_through'),
    inlineCode: mark('inlineCode'),
    link: href => (state, dispatch) => setLink(state, dispatch, href),
    bullet: list('bullet'),
    ordered: list('ordered'),
    task: list('task'),
    indent: () => sinkListItem(view.state.schema.nodes.list_item),
    outdent: () => liftListItem(view.state.schema.nodes.list_item),
    quote: () => toggleQuote,
    code: () => changeCode,
    codeLanguage: language => (state, dispatch) => {
      const { $head } = state.selection;
      if (!$head.parent.type.spec.code) return false;
      dispatch(state.tr.setNodeAttribute($head.before(), 'language', language));
      return true;
    },
    exitCode: () => exitCodeBlock,
    divider: () => insertDivider,
    table: () => insertTable,
    addRow: () => preset(addRowAfterCommand),
    addRowBefore: () => preset(addRowBeforeCommand),
    addCol: () => addColumnAfter,
    addColBefore: () => addColumnBefore,
    deleteRow: () => removeTableRows,
    deleteCol: () => deleteColumn,
    deleteTable: () => deleteTable,
    tableAlign: value => (state, dispatch) => alignColumns(state, dispatch, value),
    exitTable: () => preset(exitTable),
    undo: () => undo,
    redo: () => redo,
  };
  return {
    command(name, value, fromSlash = false) {
      if (!view.editable) return false;
      const command = commands[name](value);
      const target = fromSlash && slashAtSelection(view.state);
      const separateUndo = name === 'undo' || name === 'redo' ? view.dispatch : tr => view.dispatch(closeHistory(tr));
      const applied = target ? runAfterDeleting(view, target, command) : command(view.state, separateUndo);
      view.focus();
      return applied;
    },
    codeText() { return view.state.selection.$head.parent.textContent; },
    focus() { view.focus(); },
    slashRect() { return view.coordsAtPos(view.state.selection.head); },
    setDisabled(disabled) { view.setProps({ editable: () => !disabled }); },
    destroy() { return editor.destroy(); },
  };
}
