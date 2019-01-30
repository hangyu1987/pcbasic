"""
PC-BASIC - display.textscreen
Text operations

(c) 2013--2019 Rob Hagemans
This file is released under the GNU GPL version 3 or later.
"""

import logging
from contextlib import contextmanager

from ...compat import iterchar

from ..base import signals
from ..base import error
from ..base import tokens as tk
from ..base.tokens import ALPHANUMERIC
from .. import values


class ScrollArea(object):
    """Text viewport / scroll area."""

    def __init__(self, mode):
        """Initialise the scroll area."""
        self._height = mode.height
        self.unset()

    def init_mode(self, mode):
        """Initialise the scroll area for new screen mode."""
        self._height = mode.height
        if self._bottom == self._height:
            # tandy/pcjr special case: VIEW PRINT to 25 is preserved
            self.set(1, self._height)
        else:
            self.unset()

    @property
    def active(self):
        """A viewport has been set."""
        return self._active

    @property
    def bounds(self):
        """Return viewport bounds."""
        return self._top, self._bottom

    @property
    def top(self):
        """Return viewport top bound."""
        return self._top

    @property
    def bottom(self):
        """Return viewport bottom bound."""
        return self._bottom

    def set(self, start, stop):
        """Set the scroll area."""
        self._active = True
        # _top and _bottom are inclusive and count rows from 1
        self._top = start
        self._bottom = stop

    def unset(self):
        """Unset scroll area."""
        # there is only one VIEW PRINT setting across all pages.
        # scroll area normally excludes the bottom bar
        self.set(1, self._height - 1)
        self._active = False


class BottomBar(object):
    """Key guide bar at bottom line."""

    def __init__(self):
        """Initialise bottom bar."""
        # use 80 here independent of screen width
        # we store everything in a buffer and only show what fits
        self.clear()
        self.visible = False

    def clear(self):
        """Clear the contents."""
        self._contents = [(b' ', 0)] * 80

    def write(self, s, col, reverse):
        """Write chars on virtual bottom bar."""
        for i, c in enumerate(iterchar(s)):
            self._contents[col + i] = (c, reverse)

    def get_char_reverse(self, col):
        """Retrieve char and reverse attribute."""
        return self._contents[col]


class TextScreen(object):
    """Text screen."""

    def __init__(self, queues, values, mode, cursor, capabilities):
        """Initialise text-related members."""
        self._queues = queues
        self._values = values
        self._tandytext = capabilities in ('pcjr', 'tandy')
        # cursor
        self._cursor = cursor
        # current row and column
        # overflow: true if we're on 80 but should be on 81
        self.current_row, self.current_col, self.overflow = 1, 1, False
        # text viewport parameters
        self.scroll_area = ScrollArea(mode)
        # writing on bottom row is allowed
        self._bottom_row_allowed = False
        # function key macros
        self._bottom_bar = BottomBar()
        # initialised by init_mode
        self.mode = None
        self._attr = 0
        self._apagenum = 0
        self._vpagenum = 0
        self._pages = None
        self._apage = None

    def init_mode(
            self, mode, pages, attr, vpagenum, apagenum,
        ):
        """Reset the text screen for new video mode."""
        self.mode = mode
        self._attr = attr
        self._apagenum = apagenum
        self._vpagenum = vpagenum
        # character buffers
        self._pages = pages
        # pixel buffer
        self._apage = self._pages[self._apagenum]
        # redraw key line
        self.redraw_bar()
        # initialise text viewport & move cursor home
        self.scroll_area.init_mode(self.mode)
        self.set_pos(self.scroll_area.top, 1)

    def __repr__(self):
        """Return an ascii representation of the screen buffer (for debugging)."""
        return '\n'.join(repr(page) for page in self._pages)

    def set_page(self, vpagenum, apagenum):
        """Set visible and active page."""
        self._vpagenum = vpagenum
        self._apagenum = apagenum
        self._apage = self._pages[self._apagenum]

    def set_attr(self, attr):
        """Set attribute."""
        self._attr = attr

    def rebuild(self):
        """Completely resubmit the text and graphics screen to the interface."""
        self._cursor.rebuild()
        # redraw the text screen and rebuild text buffers in video plugin
        for page in self._pages:
            page.rebuild()


    ###########################################################################
    # basic text buffer operations

    def write_char(self, char, do_scroll_down=False):
        """Put one character at the current position."""
        # check if scroll & repositioning needed
        if self.overflow:
            self.current_col += 1
            self.overflow = False
        # see if we need to wrap and scroll down
        self._check_wrap(do_scroll_down)
        # move cursor and see if we need to scroll up
        self.check_pos(scroll_ok=True)
        # put the character
        self._apage.put_char_attr(
            self.current_row, self.current_col, char, self._attr, adjust_end=True
        )
        # move cursor. if on col 80, only move cursor to the next row
        # when the char is printed
        if self.current_col < self.mode.width:
            self.current_col += 1
        else:
            self.overflow = True
        # move cursor and see if we need to scroll up
        self.check_pos(scroll_ok=True)

    def _check_wrap(self, do_scroll_down):
        """Wrap if we need to."""
        if self.current_col > self.mode.width:
            if self.current_row < self.mode.height:
                if do_scroll_down:
                    # scroll down (make space by shifting the next rows down)
                    if self.current_row < self.scroll_area.bottom:
                        self.scroll_down(self.current_row+1)
                # wrap line
                self.set_wrap(self.current_row, True)
                # move cursor and reset cursor attribute
                self._move_cursor(self.current_row + 1, 1)
            else:
                self.current_col = self.mode.width

    def set_wrap(self, row, wrap):
        """Connect/disconnect rows on active page by line wrap."""
        self._apage.set_wrap(row, wrap)

    def wraps(self, row):
        """The given row is connected by line wrap."""
        return self._apage.wraps(row)

    def set_row_length(self, row, length):
        """Set logical length of row."""
        self._apage.set_row_length(row, length)

    def row_length(self, row):
        """Return logical length of row."""
        return self._apage.row_length(row)


    ###########################################################################
    # cursor position

    def incr_pos(self):
        """Increase the current position by a char width."""
        step = self._apage.get_charwidth(self.current_row, self.current_col)
        # on a trail byte: go just one to the right
        step = step or 1
        self.set_pos(self.current_row, self.current_col + step, scroll_ok=False)

    def decr_pos(self):
        """Decrease the current position by a char width."""
        # check width of cell to the left
        width = self._apage.get_charwidth(self.current_row, self.current_col-1)
        # previous is trail byte: go two to the left
        # lead byte: go three to the left
        if width == 0:
            step = 2
        elif width == 2:
            step = 3
        else:
            step = 1
        self.set_pos(self.current_row, self.current_col - step, scroll_ok=False)

    def move_to_end(self):
        """Jump to end of logical line; follow wraps (END)."""
        row = self._apage.find_end_of_line(self.current_row)
        if self.row_length(row) == self.mode.width:
            self.set_pos(row, self.row_length(row))
            self.overflow = True
        else:
            self.set_pos(row, self.row_length(row) + 1)

    def set_pos(self, to_row, to_col, scroll_ok=True):
        """Set the current position."""
        self.overflow = False
        self.current_row, self.current_col = to_row, to_col
        # move cursor and reset cursor attribute
        # this may alter self.current_row, self.current_col
        self.check_pos(scroll_ok)

    def check_pos(self, scroll_ok=True):
        """Check if we have crossed the screen boundaries and move as needed."""
        oldrow, oldcol = self.current_row, self.current_col
        if self._bottom_row_allowed:
            if self.current_row == self.mode.height:
                self.current_col = min(self.mode.width, self.current_col)
                if self.current_col < 1:
                    self.current_col += 1
                self._move_cursor(self.current_row, self.current_col)
                return self.current_col == oldcol
            else:
                # if row > height, we also end up here
                # (eg if we do INPUT on the bottom row)
                # adjust viewport if necessary
                self._bottom_row_allowed = False
        # see if we need to move to the next row
        if self.current_col > self.mode.width:
            if self.current_row < self.scroll_area.bottom or scroll_ok:
                # either we don't need to scroll, or we're allowed to
                self.current_col -= self.mode.width
                self.current_row += 1
            else:
                # we can't scroll, so we just stop at the right border
                self.current_col = self.mode.width
        # see if we need to move a row up
        elif self.current_col < 1:
            if self.current_row > self.scroll_area.top:
                self.current_col += self.mode.width
                self.current_row -= 1
            else:
                self.current_col = 1
        # see if we need to scroll
        if self.current_row > self.scroll_area.bottom:
            if scroll_ok:
                self.scroll()
            self.current_row = self.scroll_area.bottom
        elif self.current_row < self.scroll_area.top:
            self.current_row = self.scroll_area.top
        self._move_cursor(self.current_row, self.current_col)
        # signal position change
        return (self.current_row == oldrow and self.current_col == oldcol)

    def _move_cursor(self, row, col):
        """Move the cursor to a new position."""
        self.current_row, self.current_col = row, col
        # in text mode, set the cursor width and attriute to that of the new location
        if self.mode.is_text_mode:
            # set halfwidth/fullwidth cursor
            width = self._apage.get_charwidth(row, col)
            # set the cursor attribute
            attr = self._apage.get_attr(row, col)
            self._cursor.move(row, col, attr, width)
        else:
            # move the cursor
            self._cursor.move(row, col)


    ###########################################################################
    # clearing the screen

    def clear_view(self):
        """Clear the scroll area."""
        with self._modify_attr_on_clear():
            self._apage.clear_rows(self.scroll_area.top, self.scroll_area.bottom, self._attr)
            self.set_pos(self.scroll_area.top, 1)

    def clear(self):
        """Clear the screen."""
        with self._modify_attr_on_clear():
            self._apage.clear_rows(1, self.mode.height, self._attr)
            self.set_pos(1, 1)

    @contextmanager
    def _modify_attr_on_clear(self):
        """On some adapters, modify character attributes when clearing the scroll area."""
        if not self._tandytext:
            # keep background, set foreground to 7
            attr_save = self._attr
            self.set_attr(attr_save & 0x70 | 0x7)
            yield
            self.set_attr(attr_save)
        else:
            yield


    ###########################################################################
    # scrolling

    def scroll(self, from_row=None):
        """Scroll the scroll region up by one row, starting at from_row."""
        if from_row is None:
            from_row = self.scroll_area.top
        self._apage.scroll_up(from_row, self.scroll_area.bottom, self._attr)
        if self.current_row > from_row:
            self._move_cursor(self.current_row - 1, self.current_col)


    def scroll_down(self, from_row):
        """Scroll the scroll region down by one row, starting at from_row."""
        self._apage.scroll_down(from_row, self.scroll_area.bottom, self._attr)
        if self.current_row >= from_row:
            self._move_cursor(self.current_row + 1, self.current_col)


    ###########################################################################
    # console operations

    def find_start_of_line(self, row):
        """Find the start of the logical line that includes our current position."""
        return self._apage.find_start_of_line(row)

    def get_logical_line(self, from_row):
        """Get the contents of the logical line."""
        # find start and end of logical line
        start_row = self._apage.find_start_of_line(from_row)
        stop_row = self._apage.find_end_of_line(from_row)
        return b''.join(self._apage.get_text_bytes(start_row, stop_row+1))

    # delete

    def delete_fullchar(self):
        """Delete the character (half/fullwidth) at the current position."""
        width = self._apage.get_charwidth(self.current_row, self.current_col)
        # on a halfwidth char, delete once; lead byte, delete twice; trail byte, do nothing
        if width > 0:
            self._delete_at(self.current_row, self.current_col)
        if width == 2:
            self._delete_at(self.current_row, self.current_col)

    def _delete_at(self, row, col, remove_depleted=False):
        """Delete the halfwidth character at the given position."""
        # case 0) non-wrapping row:
        #           0a) left of or at logical end -> redraw until logical end
        #           0b) beyond logical end -> do nothing
        # case 1) full wrapping row -> redraw until physical end -> recurse for next row
        # case 2) LF row:
        #           2a) left of LF logical end ->  redraw until logical end
        #           2b) at or beyond LF logical end
        #                   -> attach next row's contents at current postion until physical end
        #                   -> if next row now empty, scroll it up & stop; otherwise recurse
        # note that the last line recurses into a multi-character delete!
        if not self.wraps(row):
            # case 0b
            if col > self.row_length(row):
                return
            # case 0a
            self._apage.delete_char_attr(row, col, self._attr)
            # if the row is depleted, drop it and scroll up from below
            if remove_depleted and self.row_length(row) == 0:
                self.scroll(row)
        elif self.row_length(row) == self.mode.width:
            # case 1
            wrap_char_attr = (
                self._apage.get_char(row+1, 0),
                self._apage.get_attr(row+1, 0)
            )
            if self.row_length(row + 1) == 0:
                wrap_char_attr = None
            self._apage.delete_char_attr(
                row, col, self._attr, wrap_char_attr
            )
            self._delete_at(row+1, 1, remove_depleted=True)
        elif col < self.row_length(row):
            # case 2a
            self._apage.delete_char_attr(row, col, self._attr)
        elif remove_depleted and col == self.row_length(row):
            # case 2b (ii) while on the first LF row deleting the last char immediately appends
            # the next row, any subsequent LF rows are only removed once they are fully empty and
            # DEL is pressed another time
            self._apage.delete_char_attr(row, col, self._attr)
        elif remove_depleted and self.row_length(row) == 0:
            # case 2b (iii) this is where the empty row mentioned at 2b (ii) gets removed
            self.scroll(row)
            return
        else:
            # case 2b (i) perform multi_character delete by looping single chars
            for newcol in range(col, self.mode.width+1):
                if self.row_length(row + 1) == 0:
                    break
                wrap_char = self._apage.get_char(row+1, 0)
                self._apage.put_char_attr(row, newcol, wrap_char, self._attr, adjust_end=True)
                self._delete_at(row+1, 1, remove_depleted=True)

    # insert

    def insert_fullchars(self, sequence):
        """Insert one or more half- or fullwidth characters and adjust cursor."""
        # insert one at a time at cursor location
        # to let cursor position logic deal with scrolling
        for c in iterchar(sequence):
            if self._insert_at(self.current_row, self.current_col, c, self._attr):
                # move cursor by one character
                # this will move to next row when necessary
                self.incr_pos()

    def _insert_at(self, row, col, c, attr):
        """Insert one halfwidth character at the given position."""
        if self.row_length(row) < self.mode.width:
            # insert the new char and ignore what drops off at the end
            # this changes the attribute of everything that has been redrawn
            self._apage.insert_char_attr(row, col, c, attr)
            # the insert has now filled the row and we used to be a row ending in LF:
            # scroll and continue into the new row
            if self.wraps(row) and self.row_length(row) == self.mode.width:
                # since we used to be an LF row, wrap == True already
                # then, the newly added row should wrap - TextBuffer.scroll_down takes care of this
                self.scroll_down(row+1)
            # if we filled out the row but aren't wrapping, we scroll & wrap at the *next* insert
            return True
        else:
            # we have therow.end == width, so we're pushing the end of the row past the screen edge
            # if we're not a wrapping line, make space by scrolling and wrap into the new line
            if not self.wraps(row) and row < self.scroll_area.bottom:
                self.scroll_down(row+1)
                self.set_wrap(row, True)
            if row >= self.scroll_area.bottom:
                # once the end of the line hits the bottom, start scrolling the start of the line up
                start = self._apage.find_start_of_line(self.current_row)
                # if we hist the top of the screen, stop inserting & drop chars
                if start <= self.scroll_area.top:
                    return False
                # scroll up
                self.scroll()
                # adjust working row number
                row -= 1
            popped_char = self._apage.insert_char_attr(row, col, c, attr)
            # insert the character in the next row
            return self._insert_at(row+1, 1, popped_char, attr)

    def clear_from(self, srow, scol):
        """Clear from given position to end of logical line (CTRL+END)."""
        end_row = self._apage.find_end_of_line(srow)
        # clear the first row of the logical line
        self._apage.clear_row_from(srow, scol, self._attr)
        # remove the additional rows in the logical line by scrolling up
        for row in range(end_row, srow, -1):
            self.scroll(row)
        self.set_pos(srow, scol)

    # line feed

    def line_feed(self):
        """Move the remainder of the line to the next row and wrap (LF)."""
        if self.current_col < self.row_length(self.current_row):
            # insert characters, preserving cursor position
            cursor = self.current_row, self.current_col
            self.insert_fullchars(b' ' * (self.mode.width-self.current_col+1))
            self.set_pos(*cursor, scroll_ok=False)
            # adjust end of line and wrapping flag - LF connects lines like word wrap
            self.set_row_length(self.current_row, self.current_col - 1)
            self.set_wrap(self.current_row, True)
            # cursor stays in place after line feed!
        else:
            # find last row in logical line
            end = self._apage.find_end_of_line(self.current_row)
            # if the logical line hits the bottom, start scrolling up to make space...
            if end >= self.scroll_area.bottom:
                # ... until the it also hits the top; then do nothing
                start = self._apage.find_start_of_line(self.current_row)
                if start > self.scroll_area.top:
                    self.scroll()
                else:
                    return
            # self.current_row has changed, don't use row var
            if self.current_row < self.mode.height:
                self.scroll_down(self.current_row+1)
            # ensure the current row now wraps
            self.set_wrap(self.current_row, True)
            # cursor moves to start of next line
            self.set_pos(self.current_row+1, 1)

    # console calls

    def clear_line(self, the_row, from_col=1):
        """Clear whole logical line (ESC), leaving prompt."""
        self.clear_from(
            self._apage.find_start_of_line(the_row), from_col
        )

    def backspace(self, prompt_row, furthest_left):
        """Delete the char to the left (BACKSPACE)."""
        row, col = self.current_row, self.current_col
        start_row = self._apage.find_start_of_line(row)
        # don't backspace through prompt or through start of logical line
        # on the prompt row, don't go any further back than we've been already
        if (
                ((col != furthest_left or row != prompt_row)
                and (col > 1 or row > start_row))
            ):
            self.decr_pos()
        self.delete_fullchar()

    def tab(self, overwrite):
        """Jump to next 8-position tab stop (TAB)."""
        newcol = 9 + 8 * int((self.current_col-1) // 8)
        if overwrite:
            self.set_pos(self.current_row, newcol, scroll_ok=False)
        else:
            self.insert_fullchars(b' ' * (newcol-self.current_col))

    def skip_word_right(self):
        """Skip one word to the right (CTRL+RIGHT)."""
        crow, ccol = self.current_row, self.current_col
        # find non-alphanumeric chars
        while True:
            c = self._apage.get_char(crow, ccol)
            if (c not in ALPHANUMERIC):
                break
            ccol += 1
            if ccol > self.mode.width:
                if crow >= self.scroll_area.bottom:
                    # nothing found
                    return
                crow += 1
                ccol = 1
        # find alphanumeric chars
        while True:
            c = self._apage.get_char(crow, ccol)
            if (c in ALPHANUMERIC):
                break
            ccol += 1
            if ccol > self.mode.width:
                if crow >= self.scroll_area.bottom:
                    # nothing found
                    return
                crow += 1
                ccol = 1
        self.set_pos(crow, ccol)

    def skip_word_left(self):
        """Skip one word to the left (CTRL+LEFT)."""
        crow, ccol = self.current_row, self.current_col
        # find alphanumeric chars
        while True:
            ccol -= 1
            if ccol < 1:
                if crow <= self.scroll_area.top:
                    # not found
                    return
                crow -= 1
                ccol = self.mode.width
            c = self._apage.get_char(crow, ccol)
            if (c in ALPHANUMERIC):
                break
        # find non-alphanumeric chars
        while True:
            last_row, last_col = crow, ccol
            ccol -= 1
            if ccol < 1:
                if crow <= self.scroll_area.top:
                    break
                crow -= 1
                ccol = self.mode.width
            c = self._apage.get_char(crow, ccol)
            if (c not in ALPHANUMERIC):
                break
        self.set_pos(last_row, last_col)

    ###########################################################################
    # bottom bar

    def update_bar(self, descriptions):
        """Update the key descriptions in the bottom bar."""
        self._bottom_bar.clear()
        for i, text in enumerate(descriptions):
            kcol = 1 + 8*i
            self._bottom_bar.write((b'%d' % (i+1,))[-1:], kcol, False)
            self._bottom_bar.write(text, kcol+1, True)

    def show_bar(self, on):
        """Switch bottom bar visibility."""
        # tandy can have VIEW PRINT 1 to 25, should raise IFC in that case
        error.throw_if(on and self.scroll_area.bottom == self.mode.height)
        self._bottom_bar.visible, was_visible = on, self._bottom_bar.visible
        if self._bottom_bar.visible != was_visible:
            self.redraw_bar()

    def redraw_bar(self):
        """Redraw bottom bar if visible, clear if not."""
        key_row = self.mode.height
        # Keys will only be visible on the active page at which KEY ON was given,
        # and only deleted on page at which KEY OFF given.
        self._apage.clear_rows(key_row, key_row, self._attr)
        if not self.mode.is_text_mode:
            reverse_attr = self._attr
        elif (self._attr >> 4) & 0x7 == 0:
            reverse_attr = 0x70
        else:
            reverse_attr = 0x07
        if self._bottom_bar.visible:
            # always show only complete 8-character cells
            # this matters on pcjr/tandy width=20 mode
            for col in range((self.mode.width//8) * 8):
                char, reverse = self._bottom_bar.get_char_reverse(col)
                attr = reverse_attr if reverse else self._attr
                self._apage.put_char_attr(key_row, col+1, char, attr)
            self.set_row_length(self.mode.height, self.mode.width)

    ###########################################################################
    # vpage text retrieval

    def print_screen(self, target_file):
        """Output the visible page to file in raw bytes."""
        if not target_file:
            return
        for line in self._pages[self._vpagenum].get_chars():
            target_file.write_line(line.replace(b'\0', b' '))

    def copy_clipboard(self, start_row, start_col, stop_row, stop_col):
        """Copy selected screen area to clipboard."""
        vpage = self._pages[self._vpagenum]
        # get all marked unicode text and clip to selection size
        text = vpage.get_text_unicode(start_row, stop_row)
        text[0] = text[0][start_col-1:]
        text[-1] = text[-1][:stop_col]
        clip_text = u'\n'.join(u''.join(_row) for _row in text)
        self._queues.video.put(signals.Event(
            signals.VIDEO_SET_CLIPBOARD_TEXT, (clip_text,)
        ))

    ###########################################################################
    # text screen callbacks

    def locate_(self, args):
        """LOCATE: Set cursor position, shape and visibility."""
        args = list(None if arg is None else values.to_int(arg) for arg in args)
        args = args + [None] * (5-len(args))
        row, col, cursor, start, stop = args
        row = self.current_row if row is None else row
        col = self.current_col if col is None else col
        error.throw_if(row == self.mode.height and self._bottom_bar.visible)
        if self.scroll_area.active:
            error.range_check(self.scroll_area.top, self.scroll_area.bottom, row)
        else:
            error.range_check(1, self.mode.height, row)
        error.range_check(1, self.mode.width, col)
        if row == self.mode.height:
            # temporarily allow writing on last row
            self._bottom_row_allowed = True
        self.set_pos(row, col, scroll_ok=False)
        if cursor is not None:
            error.range_check(0, (255 if self._tandytext else 1), cursor)
            # set cursor visibility - this should set the flag but have no effect in graphics modes
            self._cursor.set_visibility(cursor != 0)
        error.throw_if(start is None and stop is not None)
        if stop is None:
            stop = start
        if start is not None:
            error.range_check(0, 31, start, stop)
            # cursor shape only has an effect in text mode
            if self.mode.is_text_mode:
                self._cursor.set_shape(start, stop)

    def csrlin_(self, args):
        """CSRLIN: get the current screen row."""
        list(args)
        if (
                self.overflow and self.current_col == self.mode.width
                and self.current_row < self.scroll_area.bottom
            ):
            # in overflow position, return row+1 except on the last row
            csrlin = self.current_row + 1
        else:
            csrlin = self.current_row
        return self._values.new_integer().from_int(csrlin)

    def pos_(self, args):
        """POS: get the current screen column."""
        list(args)
        if self.current_col == self.mode.width and self.overflow:
            # in overflow position, return column 1.
            pos = 1
        else:
            pos = self.current_col
        return self._values.new_integer().from_int(pos)

    def screen_fn_(self, args):
        """SCREEN: get char or attribute at a location."""
        row = values.to_integer(next(args))
        col = values.to_integer(next(args))
        want_attr = next(args)
        if want_attr is not None:
            want_attr = values.to_integer(want_attr)
            want_attr = want_attr.to_int()
            error.range_check(0, 255, want_attr)
        row, col = row.to_int(), col.to_int()
        error.range_check(0, self.mode.height, row)
        error.range_check(0, self.mode.width, col)
        error.throw_if(row == 0 and col == 0)
        list(args)
        row = row or 1
        col = col or 1
        if self.scroll_area.active:
            error.range_check(self.scroll_area.top, self.scroll_area.bottom, row)
        if want_attr:
            if not self.mode.is_text_mode:
                result = 0
            else:
                result = self._apage.get_attr(row, col)
        else:
            result = self._apage.get_byte(row, col)
        return self._values.new_integer().from_int(result)

    def view_print_(self, args):
        """VIEW PRINT: set scroll region."""
        start, stop = (None if arg is None else values.to_int(arg) for arg in args)
        if start is None and stop is None:
            self.scroll_area.unset()
        else:
            if self._tandytext and not self._bottom_bar.visible:
                max_line = 25
            else:
                max_line = 24
            error.range_check(1, max_line, start, stop)
            error.throw_if(stop < start)
            self.scroll_area.set(start, stop)
            #set_pos(start, 1)
            self.overflow = False
            self._move_cursor(start, 1)
