## Progress

This got too long so I made it into it's own Markdown file.

Contained checklists are not in order. Some of these have explenations.

To the next editor reading this, split this up by component, and use catagories better, pretty please? UwU

### Implemented features

This is stuff that is either implemented already or core to the entire goal of OpenWidgets (with stuff like Adaptive Cards being an exception)

- [X] Launch (duh)
  - [X] Show QtWebEngine
- [ ] Shell integration
  - [ ] Keyboard shortcut
  - [ ] Touch screen gesture
  - [ ] Taskbar button (Windows only)
    - [ ] Static fallback (no internet)
    - [ ] Dynamic content (weather, top stories, etc)
- [ ] GPU visual flare
  - [ ] Animations
    - [ ] When opening
    - [ ] Loading feed items (skeleton)
    - [ ] When expanding or collapsing the feed
  - [ ] Materials
    - [X] Acrylic like material (currently VERY jank)
    - [X] Smoke material
  - [ ] Disable effects when only software rendering is available (e.g SwiftShader, Microsoft Basic Display Adapter)
    - [ ] Take into account `HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Windows\Dwm\ForceEffectMode` on Windows set to a DWORD of 2 (forces visual effects in Hyper-V VMs) and enable the effects anyway
- [X] Load widgets
  - [X] HTML based widgets
    - [X] Add frontend HTML into widget
    - [ ] JS exception handling
  - [ ] Adaptive Cards
    - [ ] Add card content into widget
    - [ ] Update card content periodically
  - [X] Widget management
    - [X] Save widget positions
    - [ ] Drag widget to move
    - [ ] Ellipsis menu for widget managemnt
      - This needs to be discussed further, as many things need to be taken into consideration.
      - I think a manifest option should be added (for HTML cards) and use the WinAppSDK method for Adaptive Cards, similar to Windows 11's widgets board, where we have a header with the button (and app name)
    - [ ] Manage predefined sizes
      - This needs to be discussed further, as many things need to be taken into consideration.
      - Do we allow the manifest to define what each size means? Do we define it? Do we just signal to the widget what size it needs and hope it listens?
      - We need to have at least Small, Medium, and Large for Adaptive Card compatability
- [X] Feed integration
  - [X] Ability to expand and contract feed view
  - [X] Feed settings (e.g block sources)
- [ ] Settings
  - [X] Full settings (available when feed is expanded)
    - [X] Split settings into mutiple sections
      - [ ] Manage widgets
        - [ ] Add widgets
        - [ ] Rearrange widgets
        - [ ] Remove widgets
      - [ ] Personalize feed - opens the same menu
      - [ ] Shell integration
      - TELL THE USER IF ON WIN11 TO TURN OFF WIDGETS IN SETTINGS BEFORE THEY TURN ANY OF THESE ON (include a link to  ms-settings:taskbar) but do not tell them on Win10
      - These features are currently being worked on, and aren't available yet, so while these arent disabled, and are saved, they do nothing right now
        - [ ] Toggle switch for overriding Win+W
        - [ ] Toggle switch for touch screen gesture
        - [ ] Taskbar button options
          - [ ] Toggle the taskbar button overlay
          - [ ] Toggle if it shows static (just a widgets icon) or dynamic (shows info like in Win11 22H2+)
          - [ ] Toggle open on hover
          - [ ] List of toggles of data to cycle through in dynamic
            - [ ] Weather
                - [ ] Current conditions
                - [ ] Next precip
            - [ ] News
              - [ ] Top story from PixelNews
              - [ ] Random story
            - [ ] Famous birthdays
              - [ ] Science
              - [ ] History
              - [ ] Arts
              - [ ] Computer science
              - [ ] Hackers
              - [ ] Journalists
      - [X] About
        - [X] Version - shows version of things like OpenWidget, Python, all installed widgets, etc
        - [X] Legal - OSS license, anything we need to comply with DMA and other laws and such, etc
          - [X] PixelNews
            - [X] Transparency - tells you where we get the news from for you, the details for each source, etc, where weather data is provided and how the algorythm works as it isn't there to get you hooked (ahem, TikTok, YT Shorts, etc), it picks articles to attempt to give you the best story
            - [X] Legal - anything we need to comply with DMA and other laws and such
  - [X] Settings lite (available when feed is collapsed)
    - [X] Manage widget

#### PixelNews (our equivlent to MSN News)

This section details the default feed used by OpenWidgets, PixelNews. It works the same way MSN does, it doesn't do journalism itself, it is a news ageragate. It runs on your device instead of MSN, which does agrogration inside Microsoft's servers.

- [X] Read feeds
- [X] News recommendation algorithm
- [ ] Feed settings
  - [ ] Block sources
- [ ] MSN-esque topics to follow

#### Widget API

This details the APIs offered to HTML5 widgets (not to be confused with the supported web standards.)

TODO: make this section

### Nice to haves

This is stuff that we can put aside and work on when we have time.

- [ ] Digital Markets Act compliance
  - We do not need to worry about this as much as Microsoft because we aren't marked as a gatekeeper by the EU.
  - This is still a good nice to have as it allows choice in case you do not like how the inbuilt algorithm works, if you reverse engineer MSN or Google's Discover feed and want to use that, etc
  - The weather widget and the feed currently are integrated deep in the main code. This makes it unable to switch feeds as of right now, thus violating the DMA.
- [ ] Make light mode support better
  - I use dark mode all of the time, and as such this was overlooked when I designed the UI colors.
- [ ] Offline mode
- [ ] Improve accessability

#### MSNish

- [] Missed insights (see docs/win11missedinsight.png for what I mean)

#### Security

- [ ] Disable JIT in QtWebEngine
- [ ] Limit host and path of QtWebEngine
  - Trying to change our location simply results in us going back to the local web server
- [ ] Prevent a rouge widget from modifying other widgets or the UI
  - [ ] Prevent HTML escape
  - [ ] Prevent CSS applying to other elements
  - [ ] Prevent JS from being used to do this too

#### Fun

- [ ] Add more references to *maia arson crimew*

### Currently out of scope

- [ ] Connecting to real MSN
  - Microsoft currently has no public API for MSN.
- [ ] Notifications
  - We aren't a freaking smartphone app. Did yo
- [ ] Compatability with Windows 11 Widget board providers
  - Not to be confused with Adaptive Cards, this refers to the providers themselves.
