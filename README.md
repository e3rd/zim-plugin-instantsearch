# Instant Search Plugin for Zim Wiki
Search as you type in Zim, in similar manner to OneNote Ctrl+E.

When you hit Ctrl+E, small window opens, you can type in. As you type third letter, every page that matches your search is listed. You can walk through by UP/DOWN arrow, hit Enter to stay on the page, or Esc to cancel.
Much quicker than current Zim search.

### Working with & Feedback
Known to work on:
 
* Ubuntu 15.10 – 18.10
* Win 7 Zim 0.63+
* Debian 8.9 Zim 0.62+

I'd be glad to hear from you if it's working either here in the issues or in the original bug on [launchpad](https://bugs.launchpad.net/zim/+bug/1409626).

With old Zim 0.68 you may want to use the [last release](https://github.com/e3rd/zim-plugin-instantsearch/releases/tag/1.04) which is 0.68 compatible.
### Installation
Same as for the other plugins.
* Put the instantsearch.py into the plugins folder
  * something like %appdata%\zim\data\zim\plugins in Win, or /~/.local/share/zim/plugins/ in Linux
* You enable the plugin in Zim/Edit/Preferences/Plugins/ check mark Instant search.
* Type Ctrl+E and see if it's working, or report it here

### Demonstration on YouTube
Wanna see how it looks in action? In this example, I just search for the string "linux f" twice.
[![Demonstration](https://img.youtube.com/vi/nB2SfxDhEoM/0.jpg)](https://www.youtube.com/watch?v=nB2SfxDhEoM)

### Notes

In a way the search is more reliable than current version of the internal Zim search where the query `economical` is not recognized if the part of the text is bold: `economi**cal**` (however highlighting works great), if a link is inserted in the middle: `economi[[inserted link]]cal` or if the query is hidden in the link: `[[http://economical.example.com|link]]`.