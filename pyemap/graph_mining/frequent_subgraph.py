## Copyright (c) 2017-2022, James Gayvert, Ruslan Tazhigulov, Ksenia Bravaya
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# 1. Redistributions of source code must retain the above copyright notice, this
#    list of conditions and the following disclaimer.
#
# 2. Redistributions in binary form must reproduce the above copyright notice,
#    this list of conditions and the following disclaimer in the documentation
#    and/or other materials provided with the distribution.
#
# 3. Neither the name of the copyright holder nor the names of its
#    contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

import numpy as np
from Bio.SVDSuperimposer import SVDSuperimposer
from .utils import get_graph_matcher, write_graph_smiles, make_pretty_subgraph
from networkx.drawing.nx_agraph import to_agraph
import networkx as nx
from PIL import Image
from functools import total_ordering
import tempfile


def _gen_groups(cc, all_graphs):
    groups = {}
    for group_idx, group in enumerate(cc):
        graph_list = []
        for graph_idx in group:
            graph = all_graphs[graph_idx]
            graph_list.append(graph)
        groups[group_idx + 1] = [x.graph['id'] for x in graph_list]
    return groups


@total_ordering
class SubgraphPattern():
    '''
    Stores all information regarding an identified subgraph pattern.

    Attributes
    ----------
    id: str
        Unique identifier for subgraph pattern. 
    G: :class:`networkx.Graph`
        Graph representation of this subgraph pattern.
    support: list of str
        List of PDB IDs which contain this subgraph
    protein_subgraphs: dict of str: :class:`networkx.Graph`
        Dict which contains protein subgraphs which match this pattern. Each entry has a unique identifier and a
        :class:`networkx.Graph` derived from the graphs generated by the :class:`~pyemap.emap` class which match 
        the pattern of this pattern.
    groups: dict of str: list of str
        Protein subgraph (IDs) clustered into groups based on similarity
    support_number: int
        Number of PDBs this subgraph pattern was identified in 
    
    '''

    def __init__(self, G, graph_number, support, res_to_num_label, edge_thresholds):
        '''Initializes SubgraphPattern object.

        Parameters
        ----------
        G: :class:`networkx.Graph`
            Graph representation of this subgraph pattern 
        graph_number: int
            Unique numerical ID for this subgraph pattern
        support: list of str
            List of PDB IDs which contain this subgraph
        res_to_num_label: dict of str: int
            Mapping of residue types to numerical node labels
        edge_thresholds: list of float
            Edge thresholds which define edge labels

        '''
        self.G = G.copy()
        self.support = support
        self.total_support = {}
        self.protein_subgraphs = {}
        self.unsorted_graphs = {}
        self.groups = {}
        self.res_to_num_label = res_to_num_label
        self.edge_thresholds = edge_thresholds
        self.support_number = len(support)
        self.id = str(graph_number + 1) + "_" + str(write_graph_smiles(self.G)) + "_" + str(self.support_number)
        if "#" in self.id:
            self._file_id = self.id.replace("#", "NP")
        else:
            self._file_id = self.id
        for node in self.G.nodes:
            if self.G.nodes[node]['label'] == "#":
                self.G.nodes[node]['label'] = "NP"

    def __lt__(self, other):
        if self.support_number != other.support_number:
            return self.support_number < other.support_number
        elif str(write_graph_smiles(self.G)) != str(write_graph_smiles(other.G)):
            return str(write_graph_smiles(self.G)) < str(write_graph_smiles(other.G))
        else:
            return self.G.size(weight="num_label") < other.G.size(weight="num_label")

    def __eq__(self, other):
        return self.G == other.G

    def _update_id(self, graph_number):
        self.id = str(graph_number + 1) + self.id[self.id.index('_'):]
        if "#" in self.id:
            self._file_id = self.id.replace("#", "NP")
        else:
            self._file_id = self.id

    def general_report(self):
        ''' Generates general report which describes this subgraph pattern.

        Returns
        --------
        full_str: str
            General report which describes this subgraph pattern

        '''
        full_str = ""
        full_str += "ID:" + str(self.id) + "\n"
        full_str += "Support:" + str(self.support_number) + "\n"
        full_str += "Where:" + str(list(self.support.keys())) + "\n"
        full_str += "Adjacency list:\n"
        G = self.G
        for node in G.nodes:
            full_str += G.nodes[node]['label'] + str(node) + ":["
            for neighbor in G.neighbors(node):
                full_str += G.nodes[neighbor]['label'] + str(neighbor) + "(" + str(
                    G.edges[(node, neighbor)]['num_label']) + "), "
            full_str = full_str[:-2]
            full_str += "]\n"
        return full_str

    def full_report(self):
        ''' Returns a full report of all protein subgraphs which match this pattern.

        Returns
        -------
        full_str: str
            Report of protein subgraphs which match this pattern

        '''
        full_str = self.general_report()
        if len(self.protein_subgraphs) == 0:
            full_str += "Please run `find_protein_subgraphs' to get a full report.\n"
            return full_str
        full_str += str(len(self.protein_subgraphs)) + " subgraphs matching this pattern were found.\n"
        full_str += "Graphs are classified based on " + self.clustering_option + " similarity.\n\n"
        for key in self.groups:
            graphs = self.groups[key]
            full_str += "Group " + str(key) + ": " + str(len(graphs)) + " members\n---------------\n"
            for graph in graphs:
                full_str += self._report_for_graph(self.protein_subgraphs[graph]) + "\n"
        return full_str

    def _report_for_graph(self, G):
        ''' Generates report for a given protein subgraph

        '''
        full_str = ""
        full_str += str(G.graph['id']) + "\n"
        full_str += "Nodes\n"
        for node in G.nodes:
            full_str += G.nodes[node]['label']
            full_str += " Position in alignment:" + str(G.nodes[node]['aligned_resnum'])
            full_str += "\n"
        full_str += "Adjacency list:\n"
        for node in G.nodes:
            full_str += G.nodes[node]['label'] + ":["
            for neighbor in G.neighbors(node):
                dist = '{0:.2f}'.format(G.edges[(node, neighbor)]['distance'])
                full_str += G.nodes[neighbor]['label'] + "(" + str(dist) + "), "
            full_str = full_str[:-2]
            full_str += "]\n"
        return full_str

    def visualize_subgraph_in_nglview(self,id,view):
        '''Visualize pathway in nglview widget
        
        Parameters
        ----------
        id: str
            Subgraph id to be visualized
        view: :class:`nglview.widget.NGLWidget`
            NGL Viewer widget 
        '''
        cur_subgraph = self.protein_subgraphs[id]
        cur_emap = self.support[cur_subgraph.graph['pdb_id']]
        label_texts, _, color_list, selection_strs = self._visualize_subgraph_in_ngl(cur_emap,cur_subgraph)
        for i in range(0,len(selection_strs)):
            first_atm_select = selection_strs[i][:selection_strs[i].index(')')+1]+"]"
            view.add_representation('ball+stick',sele=selection_strs[i],color=color_list[i])
            view.add_representation('label',color="black",sele=first_atm_select,labelText=label_texts[i])


    def _visualize_subgraph_in_ngl(self, emap, G):
        ''' Gets visualization of subgraph in NGL viewer

        Parameters
        ----------
        emap: :class:`~pyemap.emap`
            :class:`~pyemap.emap` object containing the protein subgraph
        G: class:`networkx.Graph`
            Protein subgraph to visualize in NGL viewer

        '''
        colors = {"F": "orange", "Y": "blue", "W": "red", "H": "green"}
        label_texts = []
        labeled_atoms = []
        color_list = []
        selection_strs = []
        for res in G.nodes:
            label_texts.append(res)
            try:
                if res not in emap.eta_moieties:
                    color_list.append(colors[res[0]])
                    labeled_atoms.append(".CA")
                else:
                    color_list.append("pink")
                    labeled_atoms.append(next(emap.residues[res].get_atoms()).name)
            except KeyError:
                color_list.append("pink")
                labeled_atoms.append(next(emap.residues[res].get_atoms()).name)
            selection_strs.append(emap.residues[res].ngl_string)
        return label_texts, labeled_atoms, color_list, selection_strs

    def find_protein_subgraphs(self, clustering_option="structural"):
        ''' Finds protein subgraphs which match this pattern.

        This function must be executed to analyze protein subgraphs.

        Parameters
        -----------
        clustering_option: str, optional
            Either 'structural' or 'sequence'

        Notes
        ------
        Graphs are clustered by both sequence and structrual similarity, and the results are stored in 
        `self._structural_groups` and `self._sequence_groups`. The clustering_option argument used here determines 
        which one of these groupings is used for `self.groups`. This can be changed 
        at any time by calling :func:`~pyemap.graph_mining.SubgraphPattern.set_clustering` and specifying the 
        other clustering option.
        '''
        self.groups = {}
        self.protein_subgraphs = {}
        all_graphs = []
        for pdb_id in self.support:
            all_graphs += self._find_subgraph_in_pdb(pdb_id)
        all_graphs.sort(key=lambda x: x.size(weight="weight"))
        for i, graph in enumerate(all_graphs):
            unique_id = graph.graph['pdb_id'] + "_" + str(i + 1)
            graph.graph['id'] = unique_id
            self.protein_subgraphs[unique_id] = graph
        if len(all_graphs) > 1:
            self._do_clustering(all_graphs)
            self.set_clustering(clustering_option)
        else:
            self.groups[1] = [x.graph['id'] for x in all_graphs]
            self.clustering_option = clustering_option
            self._structural_groups = self.groups
            self._sequence_groups = self.groups

    def set_clustering(self, clustering_option):
        ''' Sets clustering option.

        Parameters
        ----------
        clustering_option: str
            Either 'structural' or 'sequence'.

        Notes
        ------
        Since both types of clustering are always computed by :func:`pyemap.graph_mining.SubgraphPattern.find_protein_subgraphs`
        all this function actually does is swap some private variables. The purpose of this function is to determine what kind of clustering 
        gets shown in the report.
        '''
        if clustering_option == "structural":
            self.groups = self._structural_groups
        elif clustering_option == "sequence":
            self.groups = self._sequence_groups
        else:
            raise Exception("Either structural or sequence.")
        self.clustering_option = clustering_option

    def _do_clustering(self, all_graphs):
        '''Compute the supergraphs and find the connected components'''
        num_graphs = len(all_graphs)
        num_nodes = len(all_graphs[0].nodes)
        G_seq = nx.Graph()
        G_struct = nx.Graph()
        for i in range(0, len(all_graphs)):
            G_seq.add_node(i)
            G_struct.add_node(i)
        seq_sum = 0
        rmsd_sum = 0
        for i in range(0, num_graphs):
            for j in range(i + 1, num_graphs):
                GM = get_graph_matcher(all_graphs[i], all_graphs[j])
                rmsds = []
                seq_dists = []
                for mapping in GM.subgraph_isomorphisms_iter():
                    seq_dists.append(self._subgraph_seq_dist(all_graphs[i], all_graphs[j], mapping))
                    rmsds.append(self._subgraph_rmsd(all_graphs[i], all_graphs[j], mapping))
                seq_dist = np.min(seq_dists)
                rmsd = np.min(rmsds)
                seq_sum += seq_dist
                rmsd_sum += rmsd
                if seq_dist < num_nodes:
                    G_seq.add_edge(i, j)
                if rmsd <= 0.5:
                    G_struct.add_edge(i, j)
        self._structural_groups = _gen_groups(
            [c for c in sorted(nx.connected_components(G_struct), key=len, reverse=True)], all_graphs)
        self._sequence_groups = _gen_groups([c for c in sorted(nx.connected_components(G_seq), key=len, reverse=True)],
                                            all_graphs)

    def _subgraph_seq_dist(self, sg1, sg2, mapping):
        ''' Computes sequence distance between two protein subgraphs

        '''
        total_dist = 0
        for key, val in mapping.items():
            if sg1.nodes[key]['aligned_resnum'] != "X":
                total_dist += np.absolute(sg1.nodes[key]['aligned_resnum'] - sg2.nodes[val]['aligned_resnum'])
        return total_dist

    def _subgraph_rmsd(self, sg1, sg2, mapping):
        ''' Computes RMSD between two protein subgraphs

        '''
        emap1 = self.support[sg1.graph['pdb_id']]
        emap2 = self.support[sg2.graph['pdb_id']]
        atoms1 = []
        atoms2 = []
        for key, val in mapping.items():
            res1 = emap1.residues[key]
            res2 = emap2.residues[val]
            if 'CA' in res1 and 'CA' in res2:
                atoms1.append(res1['CA'].coord)
                atoms2.append(res2['CA'].coord)
            else:
                shared_id = None
                for atm in res1:
                    if atm.id in res2:
                        shared_id = atm.id
                        break
                if shared_id is not None:
                    atoms1.append(res1[shared_id].coord)
                    atoms2.append(res2[shared_id].coord)
                else:
                    return float("inf")
        if len(atoms1) >= 2 and len(atoms1) == len(atoms2):
            si = SVDSuperimposer()
            si.set(np.array(atoms1), np.array(atoms2))
            si.run()
            return si.get_rms()
        else:
            return float("inf")

    def _find_subgraph_in_pdb(self, pdb_id):
        ''' Finds all monomorphisms of this subgrpah class in a given PDB.

        Parameters
        -----------
        pdb_id: str
            PDB ID of graph to be searched
        
        Returns
        --------
        sgs: list of :class:`networkx.Graph`        

        '''
        GM = get_graph_matcher(self.support[pdb_id].init_graph, self.G)
        subgraph_isos = GM.subgraph_monomorphisms_iter()
        sgs = []
        degree_dicts = []
        for mapping in subgraph_isos:
            sg = self._generate_protein_subgraph(mapping, self.support[pdb_id].init_graph, self.G,
                                                 self.support[pdb_id])
            # eliminate redundant subgraphs
            degree_dict = dict(sg.degree)
            if degree_dict not in degree_dicts:
                degree_dicts.append(degree_dict)
                sgs.append(sg)
        self.total_support[pdb_id] = len(sgs)
        return sgs

    def _generate_protein_subgraph(self, mapping, protein_graph, G, emap_obj):
        ''' Generates protein subgraph for a given monomorphism

        Parameters
        -----------
        mapping: dict of int:int
            Mapping of nodes in protein graph and generic subgraph
        protein_graph: :class:`networkx.Graph`
            Protein graph generated by pyemap
        G: :class:`networkx.Graph`
            Graph corresponding to subgraph pattern
        emap_obj: :class:`~pyemap.emap`
            eMap object corresponding to protein graph

        Returns
        --------
        protein_subgraph: :class:`networkx.Graph`
            Protein subgraph corresponding to mapping

        '''
        mapping = dict((v, k) for k, v in mapping.items())
        protein_subgraph = G.copy()
        protein_subgraph = nx.relabel_nodes(protein_subgraph, mapping)
        # the access order is determined by residue number, maybe this will help
        sorted_graph = nx.Graph()
        for node in sorted(protein_subgraph.nodes(), key=lambda n: emap_obj.residues[n].full_id[3][1]):
            sorted_graph.add_node(node)
            sorted_graph.nodes[node]['shape'] = protein_graph.nodes[node]['shape']
            sorted_graph.nodes[node]['label'] = str(node)
            sorted_graph.nodes[node]['num_label'] = protein_graph.nodes[node]['num_label']
            sorted_graph.nodes[node]['aligned_resnum'] = emap_obj.residues[node].aligned_residue_number
            sorted_graph.nodes[node]['resnum'] = emap_obj.residues[node].full_id[3][1]
            sorted_graph.graph['pdb_id'] = protein_graph.graph['pdb_id']
        for edge in protein_subgraph.edges():
            sorted_graph.add_edge(edge[0], edge[1])
            for key in protein_graph.edges[edge]:
                sorted_graph.edges[edge][key] = protein_graph.edges[edge][key]
        return sorted_graph

    def subgraph_to_Image(self, id=None):
        '''Returns PIL image of subgraph pattern or protein subgraph

        Parameters
        -----------
        id: str, optional
            Protein subgraph ID. If not specified, generic subgraph pattern will be drawn

        Returns
        --------
        img: :class:`PIL.Image.Image`
        '''
        if id is None:
            G = self.G.copy()
        else:
            G = self.protein_subgraphs[id].copy()
        make_pretty_subgraph(G)
        agraph = to_agraph(G)
        agraph.graph_attr.update()
        agraph.edge_attr.update(len='1.0')
        fout = tempfile.NamedTemporaryFile(suffix=".png")
        agraph.draw(fout.name, prog='dot')
        img = Image.open(fout.name)
        return img

    def subgraph_to_file(self, id=None, dest=""):
        '''Saves image of subgraph pattern or protein subgraph to file

        Parameters
        -----------
        id: str, optional
            Protein subgraph ID. If not specified, generic subgraph pattern will be drawn
        dest; str,optional
            Destination to save the graph
        '''
        if id is None:
            temp_G = make_pretty_subgraph(self.G.copy())
            if dest == "":
                dest = self.id + ".png"
        else:
            temp_G = make_pretty_subgraph(self.protein_subgraphs[id])
            if dest == "":
                dest = self.id + "_" + id + ".png"
        agraph = to_agraph(temp_G)
        agraph.graph_attr.update()
        agraph.edge_attr.update(len='1.0')
        agraph.draw(dest, prog='dot')