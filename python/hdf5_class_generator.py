 #!/usr/bin/env python
import os
import string
import pickle
import subprocess
import re
from collections import OrderedDict 
import copy
from pprint import pprint
from sys import exit
from podio_config_reader import PodioConfigReader, ClassDefinitionValidator
from podio_templates import declarations, implementations
thisdir = os.path.dirname(os.path.abspath(__file__))


class ClassGenerator(object):

	def __init__(self, yamlfile, install_dir, package_name, verbose=True, dryrun=False):

		#print 'ClassGenerator __init__ TRIGGERED\n'
		self.yamlfile = yamlfile
		self.install_dir = install_dir
		self.package_name = package_name
		self.template_dir = os.path.join(thisdir, "../templates")
		self.verbose = verbose
		self.buildin_types = ClassDefinitionValidator.buildin_types
		self.created_classes = []
		self.requested_classes = []
		self.reader = PodioConfigReader(yamlfile)
		self.warnings = []
		self.component_members = {}
		self.dryrun = dryrun
		self.predef = ['int','long','char', 'float', 'double', 'long']
		self.unique_flat_members = {}
	


	def configure_clang_format(self, apply):
		if not apply:
		    self.clang_format = []
		    return
		try:
		    cformat_exe = subprocess.check_output(['which', 'clang-format']).strip()
		except subprocess.CalledProcessError:
		    print ("ERROR: Cannot find clang-format executable")
		    print ("       Please make sure it is in the PATH.")
		    self.clang_format = []
		    return
		self.clang_format = [cformat_exe, "-i",  "-style=file", "-fallback-style=llvm"]


	def process(self):
		self.reader.read()
		self.getSyntax = self.reader.options["getSyntax"]
		self.expose_pod_members = self.reader.options["exposePODMembers"]
		self.get_unique_flat_members(self.reader.components)
		self.process_components(self.reader.components)
		


	def get_unique_flat_members(self,obj):
	    unfolded = self.unfold_definitions(obj)
	    for varName, dtype in unfolded.items():
		track = []
		flattened_members = {}
		self.flatten(unfolded[varName]['Members'], track, flattened_members)
		
		# we want to make names as unique as possible
		for k, v in flattened_members.items():
		    n_key = varName + "." + k
		    self.unique_flat_members[n_key] = v


	def dot_to_underscore(self,superVarName):
		return superVarName.replace('.', '_')


	def is_child(self, members):
		"""
		This is not a child 

		{'y': 'double', 'x': 'int', 'data': {'y': 'int', 'x': 'int', 'z': 'int'}, 'p': 'std::array<int, 4>'}}

		since data has a dict object as dataype.
		"""
		child = True
		if isinstance(members, dict):        
			for varNames, dtype in members.items():
				if isinstance(dtype, dict):
					child = False
					break 
		return child


	def unfold_definitions(self,obj):
		"""
		Example:
		{'SimpleStruct': {'Members': {'y': 'int', 'x': 'int', 'z': 'int'}, 'p': 'std::array<int, 4>'} },
		 'NotSoSimpleStruct': {'Members': {'y': 'double', 'x': 'int', 'data': 'SimpleStruct'} } }

				    ||
				    ||
				   \  /
				    \/

		{'SimpleStruct': {'Members': {'y': 'int', 'x': 'int', 'z': 'int'}, 'p': 'std::array<int, 4>'} },
		 'NotSoSimpleStruct': {'Members': {'y': 'double', 'x': 'int', 'data': {'y': 'int', 'x': 'int', 'z': 'int'}, 'p': 'std::array<int, 4>'}} } }
		"""

		unfolded = copy.deepcopy(obj)

		for name, comps in obj.items():
			members = comps['Members']
			for varName, dtype in members.items():
				if varName!= 'ExtraCode':
					if dtype not in self.predef and not 'std::array' in dtype:
						# then we need to unfold it
						unfolded[name]['Members'].pop(varName,None)
						new_name = varName + '.' + dtype 
					    	unfolded[name]['Members'][new_name] = unfolded[dtype]['Members']

		# I also want to get rid of extra code
		for name,comps in unfolded.items():
			members = comps['Members']
			members.pop('ExtraCode', None)
		return unfolded	


	def flatten(self,unfolded_obj, track, res):
		"""
		Example:
		{'y': 'double', 'x': 'int', 'data': {'y': 'int', 'x': 'int', 'z': 'int'}, 'p': 'std::array<int, 4>'}
				    ||
				    ||
				   \  /
				    \/

		{'y': 'double', 'x': 'int', 'data.y':'int', 'data.x': 'int', 'data.z': 'int', 'p': 'std::array<int, 4>'}
		"""
		for varName,dtype in unfolded_obj.items():
			track.append(varName)
			if self.is_child(dtype):
				if isinstance(dtype, dict):
				    for k,v in dtype.items():
				        new_key = ".".join(track) + "." + k
				        res[new_key] = v
				    track.pop()

				else:
				    new_key = ".".join(track)
				    res[new_key] = dtype
				    track.pop()
			else:
				self.flatten(unfolded_obj[varName], track, res)

	def remove_varNames(self,split_list):
		result = []
		for x in split_list:
			if x in self.reader.components:
				result.append(x)
		return result

	def working_set(self, obj, name):
		"""
		{'SimpleStruct.y': 'double',
		 'SimpleStruct.p': 'std::array<int, 4>',
		 'SimpleStruct.x': 'int',
		'NotSoSimpleStruct.data.SimpleStruct.p': 'std::array<int, 4>',
		'NotSoSimpleStruct.data.SimpleStruct.x': 'int',
		'NotSoSimpleStruct.data.SimpleStruct.y': 'int',
		'NotSoSimpleStruct.data.SimpleStruct.z': 'int',
		'NotSoSimpleStruct.x': 'int',
		'NotSoSimpleStruct.y': 'double'}  
				    ||
				    || 			name = NotSoSimpleStruct
				   \  /
				    \/

		{'NotSoSimpleStruct.data.SimpleStruct.p': 'std::array<int, 4>',
		 'NotSoSimpleStruct.data.SimpleStruct.x': 'int',
		 'NotSoSimpleStruct.data.SimpleStruct.y': 'int',
		 'NotSoSimpleStruct.data.SimpleStruct.z': 'int',
		 'NotSoSimpleStruct.x': 'int',
		 'NotSoSimpleStruct.y': 'double'}			
		"""
		result = {}
		for k,v in obj.items():
			if k.find(name) == 0:
				result[k] = v
		return result 



	def order_by_class(self,flattened_members):
		"""
		{'NotNotSoSimpleStruct.w.NotSoSimpleStruct.data.SimpleStruct.p': 'std::array<int, 4>',
		 'NotNotSoSimpleStruct.w.NotSoSimpleStruct.data.SimpleStruct.x': 'int',
		 'NotNotSoSimpleStruct.w.NotSoSimpleStruct.data.SimpleStruct.y': 'int',
		 'NotNotSoSimpleStruct.w.NotSoSimpleStruct.data.SimpleStruct.z': 'int',
		 'NotNotSoSimpleStruct.w.NotSoSimpleStruct.x': 'int',
		 'NotNotSoSimpleStruct.w.NotSoSimpleStruct.y': 'double',
		 'NotNotSoSimpleStruct.x.SimpleStruct.p': 'std::array<int, 4>',
		 'NotNotSoSimpleStruct.x.SimpleStruct.x': 'int',
		 'NotNotSoSimpleStruct.x.SimpleStruct.y': 'int',
		 'NotNotSoSimpleStruct.x.SimpleStruct.z': 'int',
		 'NotNotSoSimpleStruct.y': 'double',
		 'NotNotSoSimpleStruct.z': 'std::array<int, 1000>',
		 'NotSoSimpleStruct.data.SimpleStruct.p': 'std::array<int, 4>',
		 'NotSoSimpleStruct.data.SimpleStruct.x': 'int',
		 'NotSoSimpleStruct.data.SimpleStruct.y': 'int',
		 'NotSoSimpleStruct.data.SimpleStruct.z': 'int',
		 'NotSoSimpleStruct.x': 'int',
		 'NotSoSimpleStruct.y': 'double',
		 'SimpleStruct.p': 'std::array<int, 4>',
		 'SimpleStruct.x': 'int',
		 'SimpleStruct.y': 'int',
		 'SimpleStruct.z': 'int'}

		}
		"""
		equivalent_class = OrderedDict()
		temp = OrderedDict() 
		max_count = max([k.count('.') for k in flattened_members])
		for i in range(0, max_count, 2):
			equivalent_class[i] = []
			temp[i] = []



		# first organize them classes 0, 2, 4...
		for k, v in flattened_members.items():
			class_count = max_count - k.count('.')
			equivalent_class[class_count].append((k,v))


		#pprint(dict(equivalent_class))

		order_sets = []

		for flat_name, dtype in equivalent_class[0]:
			clean_order = self.remove_varNames(flat_name.split('.'))
			if clean_order[::-1] not in order_sets:
				order_sets.append(clean_order[::-1])


		for sequence in order_sets:
			for name in sequence:
				type_name = '.' + name + '.'
				#print 'Type Name', type_name
				for k, v in flattened_members.items():
					
					if type_name in k:
						new_key = k[:k.find(type_name)]
						#print k, "New Key", new_key
						flattened_members[new_key] = 'mtype' + '_' + name 
						flattened_members.pop(k, None)



		return (order_sets, flattened_members)

	def const_lines(self,flattened_members):
		final_buffer = ''
		for superVar, dtype in flattened_members.items():
			name = superVar.split('.')[1]
			final_buffer += 'const H5std_string {}("{}");\n'.format(self.dot_to_underscore(superVar), name)

		return final_buffer 




	def write_hdf5_component(self, name):
		
		d = OrderedDict()
		
		header_dir = os.path.join(thisdir, self.install_dir,self.package_name,name)
		
		includes = ['// this is generated by podio_class_generator.py\n\n#include "{}.h"\n'.format(header_dir,name),\
					 '// header required for HDF5\n\n#include "H5Cpp.h"\n',\
					"// for shared pointers\n\n#include <memory>\n", \
					"// for printing messages\n\n#include <iostream>\n\n"]
					
		namespace = ['using namespace H5;\n\n']
		
		# need to declare the strings for setting up the struct
		const_dec = ['const H5std_string FILE_NAME("{}_to_HDF5.h5");\n'.format(name), \
						'const H5std_string DATASET_NAME("{}_data");\n'.format(name)]

		# fill the const_dec with each variable in the struct
		# also get array dimensions if any and insert
		# but instead of iterating on the member.items() we will iterate on the members
		# obtained from the unfolded method

		# TODO: after this we should have a unique_flat_members, with array_dims	
                
                # get a list of relevant structs required
                list_of_dependent_structs = []
                for k_name,d_name in self.reader.components[name]['Members'].items():
                    if 'std::array' not in d_name and d_name not in self.predef and 'ExtraCode' not in k_name:
                      list_of_dependent_structs.append(d_name)
                list_of_dependent_structs.append(name)

                #print "List of dependencies for name = {}".format(name)

                #print list_of_dependent_structs

                work_set = {}
                for struct_name in list_of_dependent_structs:
                    for dot_name in self.unique_flat_members:
			try:
		                first_occur = dot_name.index(struct_name)
		                if first_occur == 0:
		                    work_set[dot_name] = self.unique_flat_members[dot_name]
		        except:
		        	continue 
		        	
                #print "Working Set for name = {}".format(name)

                #print pprint(work_set)


		order_sets, flattened_members = self.order_by_class(work_set)
		
		#print "Order Set for name = {}".format(name)

                #print pprint(order_sets)
                
                #print "Flattened Set for name = {}".format(name)

                #print pprint(flattened_members)
                
		final_buffer = self.const_lines(flattened_members)
                
		# TODO: Have to figure out rank declaration. 
		rank_declaration = 'const int RANK = 1;\n'
		

		before_main = "".join(includes + namespace + const_dec) + rank_declaration + final_buffer 
		
		# Now we write the main function
		generic = "int main(int argc, char** argv)"+\
			"{\n"+ \
			"\tif(argc !=2)\n"+\
			"\t\t{\n"+\
				'\t\t\tstd::cout<<"Wrong Input. Run ./SimpleStruct <int size>\\n";\n'+ \
				'\t\t\texit(1);\n' +\
			'\t\t}\n'+\
		'\tconst long long unsigned int SIZE = atoi(argv[1]);\n'
		
		# create an array with elements of type name
		array_dec = "\tstruct {}* p = (struct {}*)malloc(SIZE * sizeof(struct {}));\n".format(name, name, name)

		comp_dec = ''
		struct_array_dec = ''

		order_sets = reduce(lambda x,y: x+y,order_sets)
		
		#print "After reduce order set for name = {}".format(name)

                #print pprint(order_sets)

		index = order_sets.index(name)
		useful_set = order_sets[:index+1] 

		for struct_name in useful_set:
			# create an array with elements of type struct_name
			array_dec = "\tstruct {}* p = (struct {}*)malloc(SIZE * sizeof(struct {}));\n".format(struct_name, struct_name, struct_name)
						
			# declare dimension of arrays if any for compound type

			wking_set = self.working_set(flattened_members, struct_name)	
			for varName, dtype in wking_set.items():
				if 'std::array' in dtype:			
					new_var = self.dot_to_underscore(varName)
					d = re.findall(r'\d+', dtype)[0]
					struct_array_dec += '\thsize_t %s_array_dim[] = {%s};\n' % (new_var, d)

			comp_dec += '\tCompType mtype_{}(sizeof({}));\n'.format(struct_name, struct_name)
			# c++ to hdf5 datatype map
			dtype_map = {'int': 'PredType::NATIVE_INT',     \
						'double': 'PredType::NATIVE_DOUBLE',\
						'long': 'PredType::NATIVE_LONG',    \
						'char': 'PredType::NATIVE_CHAR',    \
						'float': 'PredType::NATIVE_FLOAT'}
			# different map for array type
			a_type_map = {'int': 'H5T_NATIVE_INT', \
				'double': 'H5T_NATIVE_DOUBLE',		\
				'long': 'H5T_NATIVE_LONG',			\
				'char': 'H5T_NATIVE_CHAR',			\
				'float': 'H5T_NATIVE_FLOAT'}

			for superVar, dtype in wking_set.items():
				varName = superVar.split('.')[1]
				# standard datatype				
				if dtype in dtype_map:
					hdf5_dtype = dtype_map[dtype]
					comp_dec += '\tmtype_{}.insertMember({}, HOFFSET({}, {}),{});\n'.format(struct_name, self.dot_to_underscore(superVar),struct_name, varName, hdf5_dtype)

				# array datatype 
				elif 'std::array' in dtype:
					st_index = dtype.find('<') + 1
					end_index = dtype.find(',') 
					data_type = dtype[st_index:end_index].strip()
					hdf5_dtype = a_type_map[data_type]
					comp_dec += '\tmtype_{}.insertMember({}, HOFFSET({}, {}),H5Tarray_create({}, 1, {}_array_dim));\n'.format(struct_name,self.dot_to_underscore(superVar), struct_name, varName, hdf5_dtype,self.dot_to_underscore(superVar))
				# else it is a compound type
				else:
					new_line = '\tmtype_{}.insertMember({}, HOFFSET({}, {}),{});\n'.format(struct_name, self.dot_to_underscore(superVar),struct_name, varName, dtype)
					comp_dec += new_line
			

		till_now = before_main + generic + array_dec + struct_array_dec + comp_dec

		# create file
		file_dec = "\tstd::shared_ptr<H5File> file(new H5File(FILE_NAME, H5F_ACC_TRUNC));\n"
		# create dataset
		data_dec = "\thsize_t dim[] = {SIZE};\n"
		data_dec += "\tDataSpace space(RANK, dim);\n" 
		data_dec += "\tstd::shared_ptr<DataSet> dataset(new DataSet(file->createDataSet(DATASET_NAME, mtype_{}, space)));\n".format(name)
		# write data
		data_dec += '\tdataset->write(p, mtype_{});\n'.format(name) + '\treturn 0;\n}'
		
		content = till_now + file_dec + data_dec
		filename = "write_{}.cpp".format(name)	

		self.write_file(filename, content)

	def process_components(self, content):

		print 'ClassGenerator process_components TRIGGERED\n'

		self.requested_classes += content.keys()
		for name, components in content.items():
			self.create_component(name, components["Members"])
			self.write_hdf5_component(name)
			

	def create_component(self, classname, components):
	  """ 	Create a component class to be used within the data types
	      	Components can only contain simple data types and no user 
		defined ones
	  """

	  #print 'ClassGenerator create_component TRIGGERED\n'


	  namespace, rawclassname, namespace_open, namespace_close = self.demangle_classname(classname)
	  
	  '''
	  print('demangle_class returns\n')
	  
	  
	  print('namespace {}\n'.format(namespace))
	  print('rawclassname {}\n'.format(rawclassname))
	  print('namespace_open {}\n'.format(namespace_open))
	  print('namespace_close {}\n'.format(namespace_close))
	  '''
	  
	  includes = []
	  members = ""
	  extracode_declarations = ""
	  ostreamComponents = ""
	  printed = [""]
	  self.component_members[classname] = []
	  keys = sorted( components.keys() )

	  ostreamComponents +=  "inline std::ostream& operator<<( std::ostream& o,const " + classname + "& value ){ \n"

	  for name in keys:
	  	# print  " comp: " , classname , " name : " , name
		klass = components[ name ]
		if( name != "ExtraCode"):

		  if not klass.startswith("std::array"):
		    ostreamComponents +=  ( '  o << value.%s << " " ;\n' %  name  )
		  else:
		    arrsize = klass[ klass.rfind(',')+1 : klass.rfind('>') ]
		    ostreamComponents +=    '  for(int i=0,N='+arrsize+';i<N;++i)\n'
		    ostreamComponents +=  ( '      o << value.%s[i] << "|" ;\n' %  name  )
		    ostreamComponents +=    '  o << "  " ;\n'
		  klassname = klass
		  mnamespace = ""
		  if "::" in klass:
		    mnamespace, klassname = klass.split("::")
		  if mnamespace == "":
		      members+= "  %s %s;\n" %(klassname, name)
		      self.component_members[classname].append([klassname, name])
		  else:
		    members += " ::%s::%s %s;\n" %(mnamespace, klassname, name)
		    self.component_members[classname].append(["::%s::%s" % (mnamespace, klassname), name])
		  if self.reader.components.has_key(klass):
		      includes.append('#include "%s.h"\n' %(klassname))
		  if "std::array" in klass:
		      includes.append("#include <array>\n")
		      array_type = klass.split("<")[1].split(",")[0]
		      if array_type not in self.buildin_types:
		        if "::" in array_type:
		              array_type = array_type.split("::")[1]
		        includes.append("#include \"%s.h\"\n" % array_type)
		else:
		  # handle user provided extra code
		  if klass.has_key("declaration"):
		    extracode_declarations = klass["declaration"]
		  if klass.has_key("includes"):
		     includes.append(klass["includes"])

	  ostreamComponents +=  "  return o ;\n"
	  ostreamComponents +=  "}\n"
	  # make includes unique and put it in a string
	  includes = ''.join(list(set(includes)))
	  substitutions = { "ostreamComponents" : ostreamComponents,
		                "includes" : includes,
		                "members"  : members,
		                "extracode_declarations" : extracode_declarations,
		                "name"     : rawclassname,
		                "package_name" : self.package_name,
		                "namespace_open" : namespace_open,
		                "namespace_close" : namespace_close
	  }
	  self.fill_templates("Component",substitutions)
	  self.created_classes.append(classname)

	def demangle_classname(self, classname):

		#print 'ClassGenerator demangle_classname TRIGGERED\n'

		namespace_open = ""
		namespace_close = ""
		namespace = ""
		rawclassname = ""
		if "::" in classname:
		    cnameparts = classname.split("::")

		    if len(cnameparts) > 2:
		        raise Exception("'%s' defines a type with nested namespaces. Not supported, yet." % classname)
		        namespace, rawclassname = cnameparts
		        namespace_open = "namespace %s {" % namespace
		        namespace_close = "} // namespace %s" % namespace

		else:
		    rawclassname = classname
		return namespace, rawclassname, namespace_open, namespace_close

	def fill_templates(self, category, substitutions):

	  #print 'ClassGenerator fill_templates TRIGGERED\n'
	  # "Data" denotes the real class;
	  # only headers and the FN should not contain Data
	  if category == "Data":
		FN = "Data"
		endings = ("h")
	  elif category == "Obj":
		FN = "Obj"
		endings = ("h","cc")
	  elif category == "Component":
		FN = ""
		endings = ("h")
	  elif category == "Object":
		FN = ""
		endings = ("h","cc")
	  elif category == "ConstObject":
		FN = "Const"
		endings = ("h","cc")
	  elif category == "PrintInfo":
		FN = "PrintInfo"
		endings = ("h")
	  else:
		FN = category
		endings = ("h","cc")
	  for ending in endings:
		templatefile = "%s.%s.template" %(category,ending)
		templatefile = os.path.join(self.template_dir,templatefile)
		template = open(templatefile,"r").read()
		content = string.Template(template).substitute(substitutions).expandtabs(2)
		filename = "%s%s.%s" %(substitutions["name"],FN,ending)
		self.write_file(filename, content)

	def write_file(self, name,content):

	  #print 'ClassGenerator write_file TRIGGERED\n'

	  #dispatch headers to header dir, the rest to /src
	  # fullname = os.path.join(self.install_dir,self.package_name,name)
	  if name.endswith("h"):
		fullname = os.path.join(self.install_dir,self.package_name,name)
	  else:
		#print 'HDF5 file here'
		fullname = os.path.join(self.install_dir,"src",name)
	  if not self.dryrun:
		#print 'HDF5 file here dryrun'
		print('fullname = {}'.format(fullname))
		open(fullname, "w").write(content)
		if self.clang_format:
		  subprocess.call(self.clang_format + [fullname])





##########################
if __name__ == "__main__":

    from optparse import OptionParser

    usage = """usage: %prog [options] <description.yaml> <targetdir> <packagename>
    Given a <description.yaml>
    it creates data classes
    and a LinkDef.h file in
    the specified <targetdir>:
      <packagename>/*.h
      src/*.cc"""

    parser = OptionParser(usage)
    parser.add_option("-q", "--quiet",
                    action="store_false", dest="verbose", default=True,
                    help="Don't write a report to screen")
    parser.add_option("-d", "--dryrun",
                    action="store_true", dest="dryrun", default=False,
                    help="Do not actually write datamodel files")
    parser.add_option("-c", "--clangformat", dest="clangformat",
                    action="store_true", default=False,
                    help="Apply clang-format when generating code (with -style=file)")
    (options, args) = parser.parse_args()

    if len(args) != 3:
      parser.error("incorrect number of arguments")



    #--- create output directories if they do not exist
    install_path = args[1]
    project = args[2]
    directory = os.path.join( install_path ,"src" )
    if not os.path.exists( directory ):
      os.makedirs(directory)
    directory = os.path.join( install_path , project )

    print("Something happened", install_path, project)


    if not os.path.exists( directory ):
      os.makedirs(directory)

    gen = ClassGenerator(args[0], args[1], args[2], verbose=options.verbose, dryrun=options.dryrun)
    gen.configure_clang_format(options.clangformat)
    gen.process()
    for warning in gen.warnings:
      print (warning)