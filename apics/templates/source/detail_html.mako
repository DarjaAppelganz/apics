<%inherit file="../${context.get('request').registry.settings.get('clld.app_template', 'app.mako')}"/>
<%namespace name="util" file="../util.mako"/>
<%! active_menu_item = "sources" %>


<h2>${_('Source')} ${ctx.name}</h2>

<div class="tabbable">
    <ul class="nav nav-tabs">
        <li class="active"><a href="#tab1" data-toggle="tab">Text</a></li>
        <li><a href="#tab2" data-toggle="tab">BibTeX</a></li>
    </ul>
    <div class="tab-content">
        <div id="tab1" class="tab-pane active">${u.format_source(ctx)|n}</div>
        <div id="tab2" class="tab-pane">${u.format_source(ctx, 'bibtex')|n}</div>
    </div>
</div>

<%def name="sidebar()">
% if ctx.languagesource:
<%util:well title="${_('Languages')}">
    <ul class="nav nav-pills nav-stacked">
    % for source_assoc in ctx.languagesource:
        <li>${h.link(request, source_assoc.language)}</li>
    % endfor
    </ul>
</%util:well>
% endif
</%def>