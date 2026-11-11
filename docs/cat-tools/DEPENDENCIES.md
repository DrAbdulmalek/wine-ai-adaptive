# Dependency notes for Trados Studio and memoQ

> This is a planning reference, not a verified installation recipe. Dependencies vary by application release and may conflict inside a Wine prefix.

## Current recipe metadata

| Application | Wine suggestion in recipe | Listed winetricks verbs | Evidence |
|---|---|---|---|
| Trados Studio | wine-staging 9.x or newer (untested) | `dotnet48`, `corefonts`, `gdiplus`, `vcrun2019` | UNPROVEN |
| memoQ Translator Pro | wine-staging 9.x or newer (untested) | `dotnet48`, `corefonts`, `vcrun2019` | UNPROVEN |

The recipe files have `last_tested: null`. Their contents record hypotheses only. They are not commands, compatibility claims, or a recommendation to install every listed component.

## Why dependency selection must be release-specific

- .NET Framework behavior differs across application generations.
- Visual C++ runtime requirements depend on the shipped application components.
- Fonts and GDI+ may affect rendering but do not establish application compatibility.
- Trados WPF rendering, licensing/activation, plugins, and terminology-server connectivity require separate validation.
- memoQ activation and network-dependent features require separate validation.

## Safe decision rule

Do not install a dependency merely because it appears in a recipe. First identify an actual failure signature in the relevant application's log, check the vendor's requirements for that exact release, and review whether the component is supported by the chosen Wine version. Any proposed winetricks command printed by the CLI is **NOT executed** and remains a hypothesis until tested.

Never install multiple overlapping .NET or Visual C++ variants speculatively into a production prefix. Keep experiments isolated and record every change.
