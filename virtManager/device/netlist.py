# Copyright (C) 2014 Red Hat, Inc.
#
# This work is licensed under the GNU GPLv2 or later.
# See the COPYING file in the top-level directory.

from gi.repository import Gtk

import virtinst
from virtinst import log

from ..lib import uiutil
from ..baseclass import vmmGObjectUI


NET_ROW_TYPE = 0
NET_ROW_SOURCE = 1
NET_ROW_LABEL = 2
NET_ROW_SENSITIVE = 3
NET_ROW_MANUAL = 4
NET_ROW_CONNKEY = 5


def _build_row(nettype, source_name,
        label, is_sensitive, manual=False, connkey=None):
    row = []
    row.insert(NET_ROW_TYPE, nettype)
    row.insert(NET_ROW_SOURCE, source_name)
    row.insert(NET_ROW_LABEL, label)
    row.insert(NET_ROW_SENSITIVE, is_sensitive)
    row.insert(NET_ROW_MANUAL, manual)
    row.insert(NET_ROW_CONNKEY, connkey)
    return row


def _build_label_row(label, active):
    return _build_row(None, None, label, active)


def _build_manual_row(nettype, label):
    return _build_row(nettype, None, label, True, manual=True)


def _pretty_network_desc(nettype, source=None, netobj=None):
    if nettype == virtinst.DeviceInterface.TYPE_USER:
        return _("Usermode networking")

    extra = None
    if nettype == virtinst.DeviceInterface.TYPE_BRIDGE:
        ret = _("Bridge")
    elif nettype == virtinst.DeviceInterface.TYPE_VIRTUAL:
        ret = _("Virtual network")
        if netobj:
            extra = ": %s" % netobj.pretty_forward_mode()
    else:
        ret = nettype.capitalize()

    if source:
        ret += " '%s'" % source
    if extra:
        ret += " %s" % extra

    return ret


class vmmNetworkList(vmmGObjectUI):
    __gsignals__ = {
        "changed": (vmmGObjectUI.RUN_FIRST, None, []),
    }

    def __init__(self, conn, builder, topwin):
        vmmGObjectUI.__init__(self, "netlist.ui",
                              None, builder=builder, topwin=topwin)
        self.conn = conn

        self.builder.connect_signals({
            "on_net_source_changed": self._on_net_source_changed,
            "on_net_bridge_name_changed": self._emit_changed,
        })

        self._init_ui()
        self.top_label = self.widget("net-source-label")
        self.top_box = self.widget("net-source-box")

    def _cleanup(self):
        self.conn.disconnect_by_obj(self)
        self.conn = None

        self.top_label.destroy()
        self.top_box.destroy()


    ##########################
    # Initialization methods #
    ##########################

    def _init_ui(self):
        fields = []
        fields.insert(NET_ROW_TYPE, str)
        fields.insert(NET_ROW_SOURCE, str)
        fields.insert(NET_ROW_LABEL, str)
        fields.insert(NET_ROW_SENSITIVE, bool)
        fields.insert(NET_ROW_MANUAL, bool)
        fields.insert(NET_ROW_CONNKEY, str)

        model = Gtk.ListStore(*fields)
        combo = self.widget("net-source")
        combo.set_model(model)

        text = Gtk.CellRendererText()
        combo.pack_start(text, True)
        combo.add_attribute(text, 'text', NET_ROW_LABEL)
        combo.add_attribute(text, 'sensitive', NET_ROW_SENSITIVE)

        self.conn.connect("net-added", self._repopulate_network_list)
        self.conn.connect("net-removed", self._repopulate_network_list)

    def _find_virtual_networks(self):
        rows = []

        for net in self.conn.list_nets():
            nettype = virtinst.DeviceInterface.TYPE_VIRTUAL

            label = _pretty_network_desc(nettype, net.get_name(), net)
            if not net.is_active():
                label += " (%s)" % _("Inactive")

            if net.get_xmlobj().virtualport_type == "openvswitch":
                label += " (OpenVSwitch)"

            rows.append(_build_row(
                nettype, net.get_name(), label, True,
                connkey=net.get_connkey()))

        return rows

    def _populate_network_model(self, model):
        model.clear()

        def _add_manual_bridge_row():
            _nettype = virtinst.DeviceInterface.TYPE_BRIDGE
            _label = _("Bridge device...")
            model.append(_build_manual_row(_nettype, _label))

        def _add_manual_macvtap_row():
            _label = _("Macvtap device...")
            _nettype = virtinst.DeviceInterface.TYPE_DIRECT
            model.append(_build_manual_row(_nettype, _label))

        if self.conn.is_qemu_unprivileged():
            nettype = virtinst.DeviceInterface.TYPE_USER
            label = _pretty_network_desc(nettype)
            model.append(_build_row(nettype, None, label, True))
            _add_manual_bridge_row()
            return

        vnets = self._find_virtual_networks()
        for row in sorted(vnets, key=lambda r: r[NET_ROW_LABEL]):
            model.append(row)
        if not len(model):
            row = _build_label_row(_("No networking"), True)
            model.insert(0, row)

        _add_manual_bridge_row()
        _add_manual_macvtap_row()

        # If there is a bridge device, default to that
        # If not, use 'default' network
        # If not present, use first list entry
        default_bridge = virtinst.DeviceInterface.default_bridge(
                self.conn.get_backend())
        for idx, row in enumerate(model):
            nettype = row[NET_ROW_TYPE]
            source = row[NET_ROW_SOURCE]
            is_bridge = nettype == virtinst.DeviceInterface.TYPE_BRIDGE
            is_network = nettype == virtinst.DeviceInterface.TYPE_VIRTUAL

            if default_bridge:
                if is_bridge and source == default_bridge:
                    return idx
            elif is_network and source == "default":
                return idx

        return 0

    def _check_network_is_running(self, net):
        # Make sure VirtualNetwork is running
        if not net.type == virtinst.DeviceInterface.TYPE_VIRTUAL:
            return
        devname = net.source

        netobj = None
        if net.type == virtinst.DeviceInterface.TYPE_VIRTUAL:
            for n in self.conn.list_nets():
                if n.get_name() == devname:
                    netobj = n
                    break

        if not netobj or netobj.is_active():
            return

        res = self.err.yes_no(_("Virtual Network is not active."),
            _("Virtual Network '%s' is not active. "
              "Would you like to start the network "
              "now?") % devname)
        if not res:
            return

        # Try to start the network
        try:
            netobj.start()
            log.debug("Started network '%s'", devname)
        except Exception as e:
            return self.err.show_err(
                _("Could not start virtual network '%(device)s': %(error)s") % {
                    "device": devname,
                    "error": str(e),
                })

    def _find_rowiter_for_dev(self, net):
        nettype = net.type
        source = net.source
        if net.network:
            # If using type=network with a forward mode=bridge network,
            # on domain startup the runtime XML will be changed to
            # type=bridge and both source/@bridge and source/@network will
            # be filled in. For our purposes, treat this as a type=network
            source = net.network
            nettype = "network"

        def _find_row(_nettype, _source, _manual):
            for row in combo.get_model():
                if _nettype and row[NET_ROW_TYPE] != _nettype:
                    continue
                if _source and row[NET_ROW_SOURCE] != _source:
                    continue
                if _manual and row[NET_ROW_MANUAL] != _manual:
                    continue
                return row.iter

        # Find the matching row in the net list
        combo = self.widget("net-source")
        rowiter = _find_row(nettype, source, None)
        if rowiter:
            return rowiter

        # If this is a bridge or macvtap device, show the
        # manual source mode
        if nettype in [virtinst.DeviceInterface.TYPE_BRIDGE,
                       virtinst.DeviceInterface.TYPE_DIRECT]:
            rowiter = _find_row(nettype, None, True)
            self.widget("net-manual-source").set_text(source or "")
            if rowiter:
                return rowiter

        # This is some network type we don't know about. Generate
        # a label for it and stuff it in the list
        desc = _pretty_network_desc(nettype, source)
        combo.get_model().insert(0,
            _build_row(nettype, source, desc, True))
        return combo.get_model()[0].iter


    ###############
    # Public APIs #
    ###############

    def _get_network_row(self):
        return uiutil.get_list_selected_row(self.widget("net-source"))

    def get_network_selection(self):
        row = self._get_network_row()
        if not row:
            return None, None, None

        net_type = row[NET_ROW_TYPE]
        net_src = row[NET_ROW_SOURCE]
        net_check_manual = row[NET_ROW_MANUAL]

        if net_check_manual:
            net_src = self.widget("net-manual-source").get_text() or None

        mode = None
        is_direct = (net_type == virtinst.DeviceInterface.TYPE_DIRECT)
        if is_direct:
            # This is generally the safest and most featureful default
            mode = "bridge"

        return net_type, net_src, mode

    def build_device(self, macaddr, model=None):
        nettype, devname, mode = self.get_network_selection()

        net = virtinst.DeviceInterface(self.conn.get_backend())
        net.type = nettype
        net.source = devname
        net.macaddr = macaddr
        net.model = model
        net.source_mode = mode

        return net

    def validate_device(self, net):
        self._check_network_is_running(net)
        virtinst.DeviceInterface.check_mac_in_use(net.conn, net.macaddr)
        net.validate()

    def reset_state(self):
        self._repopulate_network_list()

        self.widget("net-source-warn").set_visible(False)
        self.widget("net-source-warn").set_tooltip_text("")
        self.widget("net-manual-source").set_text("")

    def set_dev(self, net):
        self.reset_state()
        rowiter = self._find_rowiter_for_dev(net)

        combo = self.widget("net-source")
        combo.set_active_iter(rowiter)
        combo.emit("changed")


    #############
    # Listeners #
    #############

    def _emit_changed(self, *args, **kwargs):
        ignore1 = args
        ignore2 = kwargs
        self.emit("changed")

    def _repopulate_network_list(self, *args, **kwargs):
        ignore1 = args
        ignore2 = kwargs

        netlist = self.widget("net-source")
        current_label = uiutil.get_list_selection(netlist, column=2)

        model = netlist.get_model()
        if not model:
            return

        try:
            if model:
                netlist.set_model(None)
                default_idx = self._populate_network_model(model)
        finally:
            netlist.set_model(model)

        for row in netlist.get_model():
            if current_label and row[2] == current_label:
                netlist.set_active_iter(row.iter)
                return

        if default_idx is None:
            default_idx = 0
        netlist.set_active(default_idx)


    def _on_net_source_changed(self, src):
        ignore = src
        self._emit_changed()
        row = self._get_network_row()
        if not row:
            return

        nettype = row[NET_ROW_TYPE]
        is_direct = (nettype == virtinst.DeviceInterface.TYPE_DIRECT)
        uiutil.set_grid_row_visible(
            self.widget("net-macvtap-warn-box"), is_direct)

        show_bridge = row[NET_ROW_MANUAL]
        uiutil.set_grid_row_visible(
            self.widget("net-manual-source"), show_bridge)
