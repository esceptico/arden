# Apple Sidebar Material Notes

Source links:

- [Apple HIG: Sidebars](https://developer.apple.com/design/Human-Interface-Guidelines/sidebars?changes=la_11)
- [Apple HIG: Materials](https://developer.apple.com/design/human-interface-guidelines/materials?changes=_11)
- [AppKit: NSVisualEffectView.Material.sidebar](https://developer.apple.com/documentation/appkit/nsvisualeffectview/material-swift.enum/sidebar?changes=_4)

## Useful Takeaways

Apple treats sidebars as navigation/control material, not as content cards. The sidebar should read as part of the window architecture: a leading material region that holds navigation, selection, and secondary controls.

Materials are used to establish hierarchy. Navigation and controls sit in a functional material layer; content sits behind or beside it. The material can be translucent, but the goal is legibility and hierarchy, not visible blur as decoration.

AppKit has a semantic `sidebar` material. That is the key signal for arden: the sidebar should have its own slate/material treatment, distinct from content, instead of being another generic floating glass surface.

## Implication For arden

The current main window feels over-framed when the sidebar is a separate rounded card inside the app window. It creates a card-within-card effect: outer window, floating sidebar slab, chat area, floating composer.

Better direction:

- one outer window shell owns the rounded corners, ring, and drop shadow
- sidebar is a full-height left slate/material region inside that shell
- no hard divider between sidebar and content unless contrast fails
- active rows use quiet slate selection, not heavy cards
- composer can remain a floating input surface because it is an active control
- settings should reuse the same slate-sidebar shape so it feels like the same app

