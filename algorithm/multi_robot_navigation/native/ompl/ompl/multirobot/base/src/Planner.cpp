/*********************************************************************
* Software License Agreement (BSD License)
*
*  Copyright (c) 2010, Rice University
*  All rights reserved.
*
*  Redistribution and use in source and binary forms, with or without
*  modification, are permitted provided that the following conditions
*  are met:
*
*   * Redistributions of source code must retain the above copyright
*     notice, this list of conditions and the following disclaimer.
*   * Redistributions in binary form must reproduce the above
*     copyright notice, this list of conditions and the following
*     disclaimer in the documentation and/or other materials provided
*     with the distribution.
*   * Neither the name of the Rice University nor the names of its
*     contributors may be used to endorse or promote products derived
*     from this software without specific prior written permission.
*
*  THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
*  "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
*  LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS
*  FOR A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE
*  COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT,
*  INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
*  BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
*  LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
*  CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
*  LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN
*  ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE
*  POSSIBILITY OF SUCH DAMAGE.
*********************************************************************/

/* Author: Justin Kottinger */

#include "ompl/multirobot/base/Planner.h"
#include "ompl/util/Exception.h"
#include "ompl/base/goals/GoalSampleableRegion.h"
#include <sstream>
#include <thread>
#include <utility>


ompl::multirobot::base::Planner::Planner(SpaceInformationPtr si, std::string name)
  : si_(std::move(si)), name_(std::move(name)), setup_(false) //, pis_(this)
{
    if (!si_)
        throw Exception(name_, "Invalid space information instance for planner");
}

const ompl::base::PlannerSpecs &ompl::multirobot::base::Planner::getSpecs() const
{
    return specs_;
}

const std::string &ompl::multirobot::base::Planner::getName() const
{
    return name_;
}

void ompl::multirobot::base::Planner::setName(const std::string &name)
{
    name_ = name;
}

const ompl::multirobot::base::SpaceInformationPtr &ompl::multirobot::base::Planner::getSpaceInformation() const
{
    return si_;
}

const ompl::multirobot::base::ProblemDefinitionPtr &ompl::multirobot::base::Planner::getProblemDefinition() const
{
    return pdef_;
}

ompl::multirobot::base::ProblemDefinitionPtr &ompl::multirobot::base::Planner::getProblemDefinition()
{
    return pdef_;
}

void ompl::multirobot::base::Planner::setProblemDefinition(const ProblemDefinitionPtr &pdef)
{
    pdef_ = pdef;
    // pis_.update();
}

// const ompl::base::PlannerInputStates &ompl::base::Planner::getPlannerInputStates() const
// {
//     return pis_;
// }

void ompl::multirobot::base::Planner::setup()
{
    if (!si_->isSetup())
    {
        OMPL_INFORM("%s: Space information setup was not yet called. Calling now.", getName().c_str());
        si_->setup();
    }
    else if (setup_)
        OMPL_WARN("%s: Planner setup called multiple times", getName().c_str());
}

void ompl::multirobot::base::Planner::checkValidity()
{
    if (!isSetup())
        setup();
    // pis_.checkValidity();
}

bool ompl::multirobot::base::Planner::isSetup() const
{
    return setup_;
}

void ompl::multirobot::base::Planner::clear()
{
    // pis_.clear();
    // pis_.update();
}

void ompl::multirobot::base::Planner::clearQuery()
{
    clear();
}

void ompl::multirobot::base::Planner::getPlannerData(ompl::base::PlannerData &data) const
{
    for (const auto &plannerProgressProperty : plannerProgressProperties_)
        data.properties[plannerProgressProperty.first] = plannerProgressProperty.second();
}

ompl::base::PlannerStatus ompl::multirobot::base::Planner::solve(const ompl::base::PlannerTerminationConditionFn &ptc, double checkInterval)
{
    return solve(ompl::base::PlannerTerminationCondition(ptc, checkInterval));
}

ompl::base::PlannerStatus ompl::multirobot::base::Planner::solve(double solveTime)
{
    if (solveTime < 1.0)
        return solve(ompl::base::timedPlannerTerminationCondition(solveTime));
    return solve(ompl::base::timedPlannerTerminationCondition(solveTime, std::min(solveTime / 100.0, 0.1)));
}

void ompl::multirobot::base::Planner::printProperties(std::ostream &out) const
{
    out << "Planner " + getName() + " specs:" << std::endl;
    out << "Multithreaded:                 " << (getSpecs().multithreaded ? "Yes" : "No") << std::endl;
    out << "Reports approximate solutions: " << (getSpecs().approximateSolutions ? "Yes" : "No") << std::endl;
    out << "Can optimize solutions:        " << (getSpecs().optimizingPaths ? "Yes" : "No") << std::endl;
    out << "Aware of the following parameters:";
    std::vector<std::string> params;
    params_.getParamNames(params);
    for (auto &param : params)
        out << " " << param;
    out << std::endl;
}

void ompl::multirobot::base::Planner::printSettings(std::ostream &out) const
{
    out << "Declared parameters for planner " << getName() << ":" << std::endl;
    params_.print(out);
}
