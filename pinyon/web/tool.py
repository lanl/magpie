"""Web views for tools"""
import nbformat
import nbconvert
from nbconvert.exporters.html import HTMLExporter
from pyramid.response import Response
from pyramid.view import view_config
import pyramid.httpexceptions as exc
import cPickle as pickle

from pinyon.transform.decision import HTMLDecisionTracker, SingleEntryHTMLDecisionTracker
from pinyon.transform.jupyter import JupyterNotebookTransformer
from pinyon.utility import WorkflowTool
from .extract import DataOutput
import pandas as pd
from ..transform.jupyter import add_data


class ToolViews:

    def __init__(self, request):
        self.request = request

    def _get_tool(self):
        """Get the tool"""
        # Get the tool
        try:
            tid = self.request.matchdict['id']
            tool = WorkflowTool.objects.get(id=tid)
            return tool, tid
        except:
            exc.HTTPNotFound(detail='No such tool: %s' % tid)

    @view_config(route_name='tool_view', renderer='template/tool_view.jinja2')
    def view(self):
        """Just view the tool"""

        tool, name = self._get_tool()

        return {
            'name': name,
            'tool': tool,
            'format_options': DataOutput.known_data_formats.keys(),
            'is_jupyter': isinstance(tool, JupyterNotebookTransformer),
            'is_decision': isinstance(tool, HTMLDecisionTracker)
        }

    @view_config(route_name='tool_run')
    def run(self):
        """Reexport data"""

        # Get user request
        tool, name = self._get_tool()

        # Check if they specified to recursively run all subsequent tools
        go_recursive = self.request.GET.get('recursive', "False")
        go_recursive = True if go_recursive.lower() == "true" else False

        # Rerun tool, and any of the following tools (if desired)
        tool.run(ignore_results=True, save_results=True, run_subsequent=go_recursive)
        tool.save()

        return exc.HTTPFound(self.request.route_url('tool_view', id=name))

    @view_config(route_name='tool_data')
    def data(self):
        """Send out data for external program"""

        # Get user request
        tool, name = self._get_tool()

        # Get desired format
        data_format = self.request.GET.get('format', 'csv')

        # Get the results of the tool
        res = tool.run(save_results=True)

        # Render into desired format
        output_settings, output_data = DataOutput.prepare_for_output(res['data'], data_format)

        # Send out the data in CSV format
        return Response(
            content_type="application/force-download",
            content_disposition='attachment; filename=%s.%s' % (tool.name, output_settings['extension']),
            body=output_data
        )

    @view_config(route_name='tool_output')
    def output(self):
        """Download an output from this tool"""

        # Get the requested tool
        tool, tid = self._get_tool()

        # Get request output
        output_name = self.request.matchdict['piece']

        # Get that object, or throw a 404
        outputs = tool.run(save_results=True)
        if output_name not in outputs:
            return exc.HTTPNotFound(detail='No such output: %s'%output_name)
        output = outputs[output_name]

        # Render that object as a pkl and return
        return Response(
            content_type="application/force-download",
            content_disposition='attachment; filename=%s.%s' % (output_name, 'pkl'),
            body=pickle.dumps(output)
        )

    @view_config(route_name='tool_jupyter')
    def render_notebook(self):
        # Get the requested tool
        tool, tid = self._get_tool()

        # If this is an IPython notebook, render it into HTML
        if not isinstance(tool, JupyterNotebookTransformer):
            return exc.HTTPNotAcceptable(detail='Tool is not a Jupyter notebook')

        # Get whether to download or view in HTML
        output_style = self.request.GET.get('format', 'html')

        # Load in the notebook
        if output_style == 'html':
            # Parse the notebook as an notebook object
            nb = nbformat.reads(tool.notebook, nbformat.NO_CONVERT)
            add_data(nb, None, None, use_placeholder=True)

            # Render it as HTML
            ex = HTMLExporter()
            output, _ = ex.from_notebook_node(nb)
            return Response(output)

        elif output_style == 'file':
            return Response(
                content_type='application/force-download',
                content_disposition='attachment; filename=%s.%s'%(tool.name, 'ipynb'),
                body=str(tool.write_notebook(None))
            )
        else:
            return exc.HTTPBadRequest(detail='Format not recognized: ' + output_style)

    @view_config(route_name='tool_edit', renderer='template/tool_edit.jinja2')
    def render_edit(self):
        errors = None

        # Get user request
        tool, name = self._get_tool()

        # Get the form result
        form = tool.get_form()(self.request.POST)

        # Process it
        if self.request.method == 'POST' and form.validate():
            tool.process_form(form, self.request)
            try:
                tool.save()
                return exc.HTTPFound(self.request.route_url('tool_view', id=name))
            except Exception, e:
                errors = e.message

        return {
            'name': name,
            'tool': tool,
            'errors': errors
        }

    @view_config(route_name='tool_decision')
    def handle_decisions(self):

        # Get user request
        tool, name = self._get_tool()

        # Check that it is a HTML decision tool
        if not isinstance(tool, HTMLDecisionTracker):
            return exc.HTTPNotAcceptable(detail='Tool is not a HTML decision tracker')

        # Check if decisions were made
        if self.request.method == 'POST':
            # Pass the output field  to the tool
            tool.process_results(self.request.params['output-field'], save_results=True)

            return exc.HTTPFound(self.request.route_url('tool_run', id=name))

        # Check if this is tool for editing a single entry, and if an entry was requested
        if 'entry' in self.request.GET and isinstance(tool, SingleEntryHTMLDecisionTracker):
            # Return the page for that entry
            key = self.request.GET['entry']
            return Response(tool.get_entry_editing_tool(key))

        return Response(tool.get_html_tool())


def includeme(config):
    config.add_route('tool_view', '/tool/{id}/view')
    config.add_route('tool_run', '/tool/{id}/run')
    config.add_route('tool_data', '/tool/{id}/data')
    config.add_route('tool_output', '/tool/{id}/output/{piece}')
    config.add_route('tool_jupyter', '/tool/{id}/jupyter')
    config.add_route('tool_edit', '/tool/{id}/edit')
    config.add_route('tool_decision', '/tool/{id}/decision')
