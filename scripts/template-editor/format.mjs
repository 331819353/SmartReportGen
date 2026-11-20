import { exitCode, setBlockType, wrapIn } from '@milkdown/prose/commands';
import { Fragment } from '@milkdown/prose/model';
import { liftListItem } from '@milkdown/prose/schema-list';
import { Selection, TextSelection } from '@milkdown/prose/state';
import { deleteRow, selectedRect } from '@milkdown/prose/tables';
import { liftTarget } from '@milkdown/prose/transform';

// 块命令以实际选中的内容为准。选区末端刚好落在下一块行首时，下一块不参与转换。
function blockSelection(selection) {
  const { $from, $to } = selection;
  if (!selection.empty && (!$from.parent.isTextblock || !$to.parent.isTextblock)) {
    const first = Selection.findFrom($from, 1, true), last = Selection.findFrom($to, -1, true);
    if (first && last && first.from <= last.to) {
      return TextSelection.create($from.doc, selection.anchor === selection.from ? first.from : last.to,
        selection.anchor === selection.from ? last.to : first.from);
    }
  }
  if (selection.empty || !$to.parent.isTextblock || $to.sameParent($from) || $to.parentOffset) return selection;
  const previous = Selection.findFrom($to.doc.resolve($to.before()), -1, true);
  if (!previous || previous.to <= $from.pos) return selection;
  return TextSelection.create($to.doc, selection.anchor === selection.from ? $from.pos : previous.to,
    selection.anchor === selection.from ? previous.to : $from.pos);
}

const isList = node => ['bullet_list', 'ordered_list'].includes(node.type.name);
const listRange = ({ $from, $to }) => $from.blockRange($to, isList);
const inTask = node => node.attrs.checked != null;

function sameListStyle(range, style) {
  if (!range || range.parent.type.name !== (style === 'ordered' ? 'ordered_list' : 'bullet_list')) return false;
  for (let i = range.startIndex; i < range.endIndex; i++) {
    if (inTask(range.parent.child(i)) !== (style === 'task')) return false;
  }
  return true;
}

export function activeList(selection) {
  const range = listRange(blockSelection(selection));
  return ['bullet', 'ordered', 'task'].find(style => sameListStyle(range, style));
}

function listAttrs(list, offset) {
  return list.type.name === 'ordered_list' ? { ...list.attrs, order: list.attrs.order + offset } : list.attrs;
}

function itemAttrs(item, style, number) {
  // 容器与列表项一起更新，防止 Milkdown 的编号同步逻辑又把项目列表转回编号列表。
  return {
    ...item?.attrs,
    listType: style === 'ordered' ? 'ordered' : 'bullet',
    label: style === 'ordered' ? `${number}.` : '•',
    spread: item?.attrs.spread ?? false,
    checked: style === 'task' ? (item?.attrs.checked ?? false) : null,
  };
}

function splitSelectedLines(tr, range) {
  const { from, to, anchor } = tr.selection;
  const breaks = [];
  for (let i = range.startIndex, pos = range.start; i < range.endIndex; i++) {
    const block = range.parent.child(i);
    if (block.isTextblock) {
      let before = null, after = null;
      block.forEach((inline, offset) => {
        if (inline.type.name !== 'hardbreak') return;
        const at = pos + 1 + offset;
        if (at < from) before = at;
        else if (at < to) breaks.push(at);
        else if (after == null) after = at;
      });
      // 首尾只拆一处边界。未选中的换行仍留在原来的正文段落中。
      if (before != null) breaks.push(before);
      if (after != null) breaks.push(after);
    }
    pos += block.nodeSize;
  }
  if (!breaks.length) return;
  for (const at of breaks.sort((a, b) => b - a)) tr.delete(at, at + 1).split(at);
  const start = tr.mapping.map(from, 1), end = Math.max(start, tr.mapping.map(to, -1));
  tr.setSelection(TextSelection.create(tr.doc, anchor === from ? start : end, anchor === from ? end : start));
}

function groupAsList(tr, range, type, style, listItem, paragraph) {
  const { $from, $to, anchor } = tr.selection;
  const items = [];
  let before = null, after = null, firstOffset = 0, lastOffset = 0;
  for (let i = range.startIndex, pos = range.start; i < range.endIndex; i++) {
    const block = range.parent.child(i);
    const first = i === range.startIndex, last = i === range.endIndex - 1;
    if (isList(block)) {
      const depth = range.depth + 1;
      const start = first ? $from.index(depth) : 0, end = last ? $to.indexAfter(depth) : block.childCount;
      if (start > 0) before = block.copy(block.content.cutByIndex(0, start));
      if (end < block.childCount) after = block.type.create(listAttrs(block, end), block.content.cutByIndex(end, block.childCount));
      block.forEach((item, offset, index) => {
        if (index < start || index >= end) return;
        if (first && index === start) firstOffset = $from.pos - pos - 1 - offset;
        if (last && index === end - 1) lastOffset = $to.pos - pos - 1 - offset;
        items.push(listItem.create(itemAttrs(item, style, items.length + 1), item.content));
      });
    } else {
      if (!block.isTextblock || block.type.spec.code) return false;
      if (first) firstOffset = $from.pos - pos + 1;
      if (last) lastOffset = $to.pos - pos + 1;
      // 列表项以段落开头。标题等块转为段落时继续保留行内内容和标记。
      items.push(listItem.create(itemAttrs(null, style, items.length + 1), paragraph.create(null, block.content)));
    }
    pos += block.nodeSize;
  }
  if (!items.length) return false;
  const list = type.create(null, items);
  const content = Fragment.fromArray([before, list, after].filter(Boolean));
  if (!range.parent.canReplace(range.startIndex, range.endIndex, content)) return false;
  const itemStart = range.start + (before?.nodeSize ?? 0) + 1;
  const start = itemStart + firstOffset;
  const end = itemStart + list.content.size - items.at(-1).nodeSize + lastOffset;
  tr.replaceWith(range.start, range.end, content);
  tr.setSelection(TextSelection.create(tr.doc, anchor === $from.pos ? start : end, anchor === $from.pos ? end : start));
  return true;
}

export function changeList(state, dispatch, style) {
  const { bullet_list, ordered_list, list_item, paragraph } = state.schema.nodes;
  const type = style === 'ordered' ? ordered_list : bullet_list;
  const selection = blockSelection(state.selection);
  const existing = listRange(selection);
  const tr = state.tr.setSelection(selection);
  if (existing) {
    const { parent, depth, start, end, startIndex, endIndex } = existing;
    if (sameListStyle(existing, style)) {
      const selectionState = state.apply(tr);
      return liftListItem(list_item)(selectionState, dispatch);
    }
    // 只拆开选中项的两侧。编号列表尾段继续原来的编号，嵌套项随各自的父项保留。
    let pos = selection.$from.before(depth);
    if (parent.type !== type) {
      if (endIndex < parent.childCount) tr.split(end, 1, [{ type: parent.type, attrs: listAttrs(parent, endIndex) }]);
      if (startIndex > 0) {
        tr.split(start, 1, [{ type: parent.type, attrs: listAttrs(parent, startIndex) }]);
        pos = start + 1;
      }
      tr.setNodeMarkup(pos, type, { spread: parent.attrs.spread });
    }
    const current = listRange(tr.selection);
    if (!current) return false;
    for (let i = current.startIndex, at = current.start; i < current.endIndex; i++) {
      const item = current.parent.child(i);
      tr.setNodeMarkup(at, null, itemAttrs(item, style, (current.parent.attrs.order ?? 1) + i));
      at += item.nodeSize;
    }
  } else {
    const range = selection.$from.blockRange(selection.$to);
    if (!range) return false;
    // 源码软换行和 Shift+Enter 都属于同一段。先按换行拆段，再合并选中的正文和已有列表项。
    splitSelectedLines(tr, range);
    const mapped = tr.selection.$from.blockRange(tr.selection.$to);
    if (!mapped || !groupAsList(tr, mapped, type, style, list_item, paragraph)) return false;
  }
  dispatch(tr.scrollIntoView());
  return true;
}

const leafText = node => node.type.spec.leafText?.(node) ?? node.attrs.alt ?? '';

export function changeCode(state, dispatch, language = '') {
  const { code_block, hardbreak, paragraph } = state.schema.nodes;
  const selection = blockSelection(state.selection);
  const { $from, $to } = selection;
  const range = $from.blockRange($to);
  if (!range) return false;
  const blocks = [];
  for (let i = range.startIndex; i < range.endIndex; i++) {
    const block = range.parent.child(i);
    if (!block.isTextblock) return false;
    blocks.push(block);
  }
  const removing = blocks.length === 1 && blocks[0].type === code_block;
  if (!range.parent.canReplaceWith(range.startIndex, range.endIndex, removing ? paragraph : code_block)) return false;
  const text = blocks.map(block => block.textBetween(0, block.content.size, '\n', leafText)).join('\n');
  const inline = text ? [state.schema.text(text)] : [];
  if (removing) {
    // Markdown 行内换行是独立节点，正文不能直接使用带换行的代码文本节点。
    inline.length = 0;
    for (const [i, line] of text.split('\n').entries()) {
      if (i) inline.push(hardbreak.create());
      if (line) inline.push(state.schema.text(line));
    }
  }
  const block = removing ? paragraph.create(null, inline) : code_block.create({ language }, inline);
  const tr = state.tr.replaceWith(range.start, range.end, block);
  // 整块转换保留原选区范围，软换行按一个字符计入代码偏移。
  const start = $from.parent.textBetween(0, $from.parentOffset, '\n', leafText).length;
  const end = text.length - $to.parent.textBetween($to.parentOffset, $to.parent.content.size, '\n', leafText).length;
  const anchor = range.start + 1 + start;
  const head = range.start + 1 + end;
  tr.setSelection(TextSelection.create(tr.doc, selection.anchor === selection.from ? anchor : head,
    selection.anchor === selection.from ? head : anchor));
  dispatch(tr.scrollIntoView());
  return true;
}

export function setHeading(state, dispatch, level) {
  const { heading, paragraph } = state.schema.nodes;
  return setBlockType(level ? heading : paragraph, level ? { level } : null)(
    state.apply(state.tr.setSelection(blockSelection(state.selection))), dispatch);
}

export function toggleQuote(state, dispatch) {
  const { blockquote } = state.schema.nodes;
  const selection = blockSelection(state.selection);
  const range = selection.$from.blockRange(selection.$to, node => node.type === blockquote);
  const tr = state.tr.setSelection(selection);
  if (!range) return wrapIn(blockquote)(state.apply(tr), dispatch);
  const target = liftTarget(range);
  if (target == null) return false;
  dispatch(tr.lift(range, target).scrollIntoView());
  return true;
}

export function insertDivider(state, dispatch) {
  const { hr, paragraph } = state.schema.nodes;
  const divider = hr.create(), next = paragraph.create();
  // 按光标插入，保留当前段落在光标两侧的内容。
  const { $from } = state.selection;
  const at = $from.depth ? $from.after() : $from.pos;
  const from = $from.parent.type === paragraph && !$from.parent.content.size ? $from.before() : at;
  const $start = state.doc.resolve(from), $end = state.doc.resolve(at);
  const blocks = Fragment.fromArray([divider, next]);
  if (!$start.parent.canReplace($start.index(), $end.index(), blocks)) return false;
  const tr = state.tr.replaceWith(from, at, blocks);
  dispatch(tr.setSelection(Selection.near(tr.doc.resolve(from + divider.nodeSize + 1))).scrollIntoView());
  return true;
}

export function alignColumns(state, dispatch, alignment) {
  const rect = selectedRect(state);
  const tr = state.tr;
  // GFM 将对齐写在表头分隔行，必须按列更新表头和正文单元格。
  for (let row = 0; row < rect.map.height; row++) {
    for (let col = rect.left; col < rect.right; col++) {
      tr.setNodeAttribute(rect.tableStart + rect.map.map[row * rect.map.width + col], 'alignment', alignment);
    }
  }
  dispatch(tr);
  return true;
}

export function removeTableRows(state, dispatch) {
  const { top, bottom, map, table, tableStart } = selectedRect(state);
  if (map.height - (bottom - top) < 2) return false;
  if (top > 0) return deleteRow(state, dispatch);

  // GitHub Flavored Markdown 需要首行表头。删掉原表头时，把第一条保留的行提升为表头。
  const { table_header, table_header_row } = state.schema.nodes;
  const cells = [];
  table.child(bottom).forEach(cell => cells.push(table_header.create(cell.attrs, cell.content)));
  const header = table_header_row.create(null, cells);
  const rest = table.content.cutByIndex(bottom + 1, map.height);
  const tr = state.tr.replaceWith(tableStart, tableStart + table.content.size, Fragment.from(header).append(rest));
  dispatch(tr.setSelection(Selection.near(tr.doc.resolve(tableStart), 1)).scrollIntoView());
  return true;
}

export function exitCodeBlock(state, dispatch) {
  const { $head } = state.selection;
  if (!$head.parent.type.spec.code) return false;
  // 退出命令对代码块中间的光标同样有效，始终在块后新建正文段落。
  return exitCode(state.apply(state.tr.setSelection(TextSelection.create(state.doc, $head.end()))), dispatch);
}

function linkRange(state) {
  const { $from, from, to, empty } = state.selection;
  const type = state.schema.marks.link;
  const mark = type.isInSet($from.marks());
  if (!empty || !mark) return { from, to, mark };
  // 光标处编辑整个链接，包括被加粗等行内格式拆成多个文本节点的链接。
  let start = from, end = to;
  const nodes = [];
  $from.parent.forEach((node, offset) => nodes.push({ node, pos: $from.start() + offset }));
  const index = nodes.findIndex(({ node, pos }) => mark.isInSet(node.marks) && pos <= from && from <= pos + node.nodeSize);
  for (let i = index; i >= 0 && mark.isInSet(nodes[i].node.marks); i--) start = nodes[i].pos;
  for (let i = index; i >= 0 && i < nodes.length && mark.isInSet(nodes[i].node.marks); i++) end = nodes[i].pos + nodes[i].node.nodeSize;
  return { from: start, to: end, mark };
}

export function linkAtSelection(state) {
  return linkRange(state).mark?.attrs.href || '';
}

export function setLink(state, dispatch, href) {
  const { link } = state.schema.marks;
  const { from, to, mark } = linkRange(state);
  const tr = state.tr.removeMark(from, to, link).removeStoredMark(link);
  if (href) {
    const updated = link.create({ ...mark?.attrs, href });
    if (from === to) tr.replaceSelectionWith(state.schema.text(href, updated), false);
    else tr.addMark(from, to, updated);
  }
  dispatch(tr.scrollIntoView());
  return true;
}
